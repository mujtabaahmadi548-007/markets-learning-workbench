"""Chart data: real candles from the asset's candle chain, stored in the `candles` table.

Ranges -> candle size:  1D -> 5m,  1W -> 1h,  1M -> 1h,  6M -> 1d,  1Y -> 1d.
Candles are fetched from a provider only when the stored ones are older than that provider's TTL
(and not at all while the market is closed and we already have data after the close), which keeps
free-plan credits low. One chart always shows ONE source (never mixed), named on the chart.
Sources with daily values only (FRED, US Treasury) are drawn as a line. They publish after the close, often the
next business day, so when the next source in the chain (Yahoo) already has a newer US trading day, its candles are
shown instead (the same rule as the price, see feeds.py); on the same day the official source is shown.
"""
from __future__ import annotations

import asyncio
import bisect
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.session import Database
from app.models.tables import AppSetting, Candle
from app.providers.base import CandleData, Provider, ProviderError, ProviderRateLimited
from app.services import market_hours as mh
from app.services.assets import AssetRT, ChainEntry
from app.services.provider_health import ProviderHealth
from app.timeutil import NEW_YORK, utcnow

log = logging.getLogger(__name__)

RANGES: dict[str, tuple[str, timedelta]] = {
    "1D": ("5m", timedelta(days=1)),
    "1W": ("1h", timedelta(days=7)),
    "1M": ("1h", timedelta(days=31)),
    "6M": ("1d", timedelta(days=183)),
    "1Y": ("1d", timedelta(days=366)),
}
FETCH_LIMIT = {"5m": 600, "1h": 800, "1d": 400}
SPARK_REFRESH = 300.0

# Ranges drawn from the full daily history (a source with has_history, i.e. Yahoo). MAX keeps every bar up to
# MAX_BARS and otherwise switches to weekly, then monthly bars, so phones never download 25,000 candles.
HISTORY_RANGES = ("YTD", "5Y", "MAX")
ALL_RANGES = (*RANGES, *HISTORY_RANGES)
HISTORY_REFRESH = timedelta(hours=20)
HISTORY_STATE_KEY = "history_fetched"
HISTORY_FLOOR = datetime(1900, 1, 1, tzinfo=timezone.utc)
MAX_BARS = 2600


def sig(x: float | None) -> float | None:
    """6 significant digits: exact for prices as quoted, and keeps tiny split-adjusted 1980s prices (0.0997)."""
    return None if x is None else float(f"{x:.6g}")


def day_tz(asset: AssetRT):  # noqa: ANN201
    """The calendar a market's daily bars belong to: UTC days for 24/7 crypto, New York trading days otherwise."""
    return timezone.utc if asset.schedule == mh.CRYPTO else NEW_YORK


def adjustment_text(asset: AssetRT) -> str:
    if asset.schedule == mh.CRYPTO:
        return ("Full history: daily US-dollar prices (days 00:00-24:00 UTC), a different market from the Binance USDT "
                "price shown live, so the newest values can differ slightly.")
    return "Prices are adjusted for stock splits but not for dividends (price return)."


def dedupe_days(rows: list[Candle], tz=NEW_YORK) -> list[Candle]:  # noqa: ANN001
    """One daily candle per market date (the newest stored one wins). tz: the market's own day, see day_tz()."""
    out: dict[Any, Candle] = {}
    for r in rows:
        out[r.ts.astimezone(tz).date()] = r
    return list(out.values())


def aggregate(bars: list[tuple], size: str, tz=NEW_YORK) -> list[tuple]:  # noqa: ANN001
    """Daily (ts, o, h, l, c, v) -> weekly ('1wk', weeks start Monday) or monthly ('1mo') bars:
    first open, highest high, lowest low, last close, summed volume; stamped with the first day's time."""
    out: list[list] = []
    key_prev = None
    for ts, o, h, lo, c, v in bars:
        d = ts.astimezone(tz).date()
        key = d.isocalendar()[:2] if size == "1wk" else (d.year, d.month)
        if key != key_prev:
            out.append([ts, o, h, lo, c, v])
            key_prev = key
        else:
            b = out[-1]
            b[2], b[3], b[4] = max(b[2], h), min(b[3], lo), c
            b[5] = (b[5] or 0) + (v or 0) if (b[5] is not None or v is not None) else None
    return [tuple(b) for b in out]


PERIODS = [("1 month", timedelta(days=30)), ("3 months", timedelta(days=91)), ("YTD", None),
           ("1 year", timedelta(days=365)), ("3 years", timedelta(days=round(3 * 365.25))),
           ("5 years", timedelta(days=round(5 * 365.25))), ("10 years", timedelta(days=round(10 * 365.25))),
           ("20 years", timedelta(days=round(20 * 365.25)))]


def performance_table(rows: list[Candle], tz=NEW_YORK) -> dict[str, Any]:  # noqa: ANN001
    ts = [r.ts for r in rows]
    last = rows[-1]

    def nyd(t: datetime) -> str:
        return t.astimezone(tz).date().isoformat()

    def at_or_before(t: datetime) -> Candle | None:
        i = bisect.bisect_right(ts, t) - 1
        return rows[i] if i >= 0 else None

    out = []
    for label, delta in PERIODS:
        if delta is None:  # YTD: from the last close of the previous year
            y = last.ts.astimezone(tz).year
            # the last bar before the year began (crypto bars are stamped 00:00 UTC, i.e. at 1 Jan itself)
            start = at_or_before(datetime(y, 1, 1, tzinfo=tz).astimezone(timezone.utc) - timedelta(seconds=1))
            years = None
        else:
            if last.ts - delta < rows[0].ts:
                continue  # the history is shorter than this period
            start = at_or_before(last.ts - delta)
            years = delta.days / 365.25
        if start is None or not start.close:
            continue
        out.append(_ret(label, start, last, years, nyd))
    span = (last.ts - rows[0].ts).days / 365.25
    out.append(_ret("All history", rows[0], last, span if span >= 1 else None, nyd))
    hi = max(rows, key=lambda r: r.close)
    lo = min(rows, key=lambda r: r.close)
    return {
        "first_date": nyd(rows[0].ts), "last_date": nyd(last.ts), "last_close": sig(last.close), "days": len(rows),
        "years": round(span, 1), "periods": out,
        "highest_close": {"close": sig(hi.close), "date": nyd(hi.ts)},
        "lowest_close": {"close": sig(lo.close), "date": nyd(lo.ts)},
        "from_high_pct": round((last.close / hi.close - 1) * 100, 2) if hi.close else None,
    }


def _ret(label: str, start: Candle, last: Candle, years: float | None, nyd) -> dict[str, Any]:  # noqa: ANN001
    change = last.close / start.close - 1
    cagr = None
    if years and years >= 1 and last.close > 0:
        cagr = round(((last.close / start.close) ** (1 / years) - 1) * 100, 2)
    return {"label": label, "start_date": nyd(start.ts), "start_close": sig(start.close),
            "change_pct": round(change * 100, 2), "cagr_pct": cagr}


class CandleService:
    def __init__(self, db: Database, providers: dict[str, Provider], health: dict[str, ProviderHealth]) -> None:
        self.db = db
        self.providers = providers
        self.health = health
        self._fetched: dict[tuple, float] = {}
        self._fetched_wall: dict[tuple, datetime] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self.spark: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------ fetching
    def _fresh(self, key: tuple, p: Provider, tf: str, schedule: str) -> bool:
        t = self._fetched.get(key)
        if t is None:
            return False
        if time.monotonic() - t < p.candle_ttl.get(tf, 600):
            return True
        # Market closed and we already fetched after the close: nothing new can exist.
        if schedule != mh.CRYPTO and not mh.is_open(schedule, utcnow()):
            last_close = mh.last_close(schedule, utcnow())
            fetched_at = self._fetched_wall.get(key)
            if last_close and fetched_at and fetched_at > last_close + timedelta(minutes=20):
                return True
        return False

    async def _ensure(self, asset: AssetRT, entry: ChainEntry, p: Provider, tf: str) -> None:
        key = (asset.id, p.name, entry.symbol, tf)
        if self._fresh(key, p, tf, asset.schedule):
            return
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._fresh(key, p, tf, asset.schedule):
                return
            # Generous timeout: it includes any wait in the provider's own rate limiter.
            candles = await asyncio.wait_for(p.get_candles(entry.symbol, tf, FETCH_LIMIT[tf]), timeout=120)
            await asyncio.to_thread(self._upsert, asset.id, tf, f"{p.name}:{entry.symbol}", candles)
            self._fetched[key] = time.monotonic()
            self._fetched_wall[key] = utcnow()
            self.health[p.name].record_success(f"candles:{entry.symbol}")

    def _upsert(self, asset_id: int, tf: str, source: str, candles: list[CandleData]) -> None:
        """Insert or update candles in batches (a full history can be 25,000 daily rows)."""
        if not candles:
            return
        rows = [dict(asset_id=asset_id, timeframe=tf, ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close,
                     volume=c.volume, source=source) for c in candles]
        stmt = sqlite_insert(Candle)
        stmt = stmt.on_conflict_do_update(index_elements=["asset_id", "timeframe", "ts", "source"],
                                          set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume")})
        with self.db.session() as s:
            for i in range(0, len(rows), 2000):
                s.execute(stmt, rows[i:i + 2000])

    def _load(self, asset_id: int, tf: str, source: str, since: datetime) -> list[Candle]:
        with self.db.session() as s:
            return list(s.scalars(select(Candle).where(
                Candle.asset_id == asset_id, Candle.timeframe == tf, Candle.source == source, Candle.ts >= since,
            ).order_by(Candle.ts)))

    # ------------------------------------------------------------------ choosing the source
    async def _rows(self, asset: AssetRT, entry: ChainEntry, tf: str, since: Callable[[Provider], datetime],
                    problems: list[str], empty_text: str) -> tuple[Provider, str, list[Candle]] | None:
        """Fetch one source's candles if due and load them from `since(provider)`. None (problem noted) if unusable."""
        p = self.providers.get(entry.provider)
        if p is None:
            return None
        if not p.configured:
            problems.append(f"{p.label}: no API key")
            return None
        h = self.health[p.name]
        if not h.symbol_available(f"candles:{entry.symbol}"):
            problems.append(f"{p.label}: cooling down after an error")
            return None
        tf_eff = "1d" if p.close_only else tf
        try:
            await self._ensure(asset, entry, p, tf_eff)
        except ProviderRateLimited as e:
            h.record_error(str(e), f"candles:{entry.symbol}", provider_wide=True)
            problems.append(str(e))
            return None
        except (ProviderError, asyncio.TimeoutError) as e:
            msg = str(e) or f"{p.label}: timed out"
            h.record_error(msg, f"candles:{entry.symbol}")
            problems.append(msg)
            return None
        except Exception as e:  # noqa: BLE001
            log.exception("candles %s %s failed", p.name, entry.symbol)
            h.record_error(f"{p.label}: {type(e).__name__}", f"candles:{entry.symbol}")
            problems.append(f"{p.label}: {type(e).__name__}")
            return None
        rows = await asyncio.to_thread(self._load, asset.id, tf_eff, f"{p.name}:{entry.symbol}", since(p))
        if not rows:
            problems.append(f"{p.label}: {empty_text}")
            return None
        return p, tf_eff, rows

    async def _pick(self, asset: AssetRT, tf: str, since: Callable[[Provider], datetime], problems: list[str],
                    empty_text: str) -> tuple[ChainEntry, Provider, str, list[Candle], str | None] | None:
        """First working source in the candle chain, except that a daily-only source (FRED, US Treasury) gives way
        to the next intraday source when that one's newest candle is from a newer US trading day.
        Returns (entry, provider, timeframe, rows, note saying why the newer source is shown)."""
        chain = asset.candle_chain
        for i, entry in enumerate(chain):
            got = await self._rows(asset, entry, tf, since, problems, empty_text)
            if got is None:
                continue
            p, tf_eff, rows = got
            if p.close_only:
                for later in chain[i + 1:]:
                    lp = self.providers.get(later.provider)
                    if lp is None or lp.close_only:
                        continue
                    # Problems of this optional comparison are not shown: the official chart is still fine.
                    alt = await self._rows(asset, later, tf, since, [], empty_text)
                    if alt is not None:
                        d = rows[-1].ts.date()  # daily values are stamped 00:00 UTC of their date
                        a = alt[2][-1].ts.astimezone(NEW_YORK).date()
                        if a > d:
                            note = (f"{p.label} has published values up to {d.day} {d:%b %Y} so far (it publishes after the "
                                    f"close, often the next business day). {lp.label} already has {a.day} {a:%b %Y}, "
                                    "so its newer chart is shown.")
                            return later, lp, alt[1], alt[2], note
                    break
            return entry, p, tf_eff, rows, None
        return None

    # ------------------------------------------------------------------ public
    async def chart(self, asset: AssetRT, rng: str) -> dict[str, Any]:
        if rng not in RANGES:
            rng = "1D"
        tf, lookback = RANGES[rng]
        problems: list[str] = []

        def span_of(p: Provider) -> timedelta:
            return max(lookback, timedelta(days=31)) if p.close_only else lookback

        picked = await self._pick(asset, tf, lambda p: utcnow() - span_of(p) - timedelta(days=4), problems,
                                  "no candles in this range")
        if picked is None:
            return {"range": rng, "candles": [], "problems": problems or ["No chart source is available for this asset."]}
        entry, p, tf_eff, rows, newer_note = picked
        rows = self._trim(rows, rng, tf_eff, asset.schedule, span_of(p))
        return {
            "range": rng, "timeframe": tf_eff, "provider": p.name, "source": p.label, "symbol": entry.symbol,
            "note": entry.note, "is_proxy": entry.proxy, "is_unofficial": p.unofficial, "close_only": p.close_only,
            "daily_only_note": p.close_only and tf != "1d", "newer_note": newer_note,
            "candles": [{"time": int(c.ts.timestamp()), "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                         "volume": c.volume} for c in rows],
            "problems": problems,
        }

    async def series(self, asset: AssetRT, tf: str, since: datetime) -> dict[str, Any]:
        """Candles of one source from `since` until now (chosen like chart()). Used for 'price since publish'.
        Daily-only sources return daily values."""
        problems: list[str] = []
        picked = await self._pick(asset, tf, lambda p: since, problems, "no candles for that period")
        if picked is None:
            return {"candles": [], "problems": problems or ["No chart source for this asset"]}
        entry, p, tf_eff, rows, newer_note = picked
        return {"provider": p.name, "symbol": entry.symbol, "source": p.label, "note": entry.note, "is_proxy": entry.proxy,
                "is_unofficial": p.unofficial, "close_only": p.close_only, "timeframe": tf_eff, "problems": problems,
                "newer_note": newer_note,
                "candles": [{"ts": c.ts, "open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in rows]}

    def _trim(self, rows: list[Candle], rng: str, tf: str, schedule: str, span: timedelta) -> list[Candle]:
        if not rows:
            return rows
        last = rows[-1].ts
        if rng == "1D" and tf == "5m" and schedule == mh.US_EQUITY:
            # Show the latest US trading session only.
            day = last.astimezone(NEW_YORK).date()
            return [r for r in rows if r.ts.astimezone(NEW_YORK).date() == day]
        # Anchor the window on the newest candle (markets that are closed still show their last stretch).
        start = last - span
        return [r for r in rows if r.ts > start]

    # ------------------------------------------------------------------ full history (YTD / 5Y / MAX + performance)
    def history_entry(self, asset: AssetRT) -> tuple[ChainEntry, Provider] | None:
        """The first source in the candle chain that has every daily candle since the first trade (Yahoo)."""
        for e in asset.candle_chain:
            p = self.providers.get(e.provider)
            if p is not None and p.configured and p.has_history and p.serves(e.symbol):
                return e, p
        return None

    def history_ranges(self, asset: AssetRT) -> list[str]:
        return list(HISTORY_RANGES) if self.history_entry(asset) else []

    def _history_stamp(self, source: str, mark: bool = False) -> datetime | None:
        """When a source's full history was last downloaded. Kept in the database, so website builds (a new process
        every ~5 minutes) download each history about once a day, not every build."""
        with self.db.session() as s:
            row = s.get(AppSetting, HISTORY_STATE_KEY)
            data = dict(row.value) if row and isinstance(row.value, dict) else {}
            if mark:
                data[source] = utcnow().isoformat()
                if row is None:
                    s.add(AppSetting(key=HISTORY_STATE_KEY, value=data, updated_at=utcnow()))
                else:
                    row.value, row.updated_at = data, utcnow()
            t = data.get(source)
            return datetime.fromisoformat(t) if t else None

    async def _ensure_history(self, asset: AssetRT, entry: ChainEntry, p: Provider) -> None:
        source = f"{p.name}:{entry.symbol}"
        lock = self._locks.setdefault((asset.id, source, "history"), asyncio.Lock())
        async with lock:
            got = await asyncio.to_thread(self._history_stamp, source)
            if got is not None and utcnow() - got < HISTORY_REFRESH:
                return
            candles = await asyncio.wait_for(p.get_history(entry.symbol), timeout=120)
            await asyncio.to_thread(self._upsert, asset.id, "1d", source, candles)
            await asyncio.to_thread(self._history_stamp, source, True)
            log.info("%s: full history from %s, %d daily candles", asset.symbol, p.label, len(candles))

    async def _history_rows(self, asset: AssetRT, since: datetime) -> tuple[ChainEntry, Provider, list[Candle], list[str]] | None:
        he = self.history_entry(asset)
        if he is None:
            return None
        entry, p = he
        problems: list[str] = []
        try:
            await self._ensure_history(asset, entry, p)
        except (ProviderError, asyncio.TimeoutError) as e:
            problems.append(f"Full history could not be refreshed: {e or 'timed out'}")
        except Exception as e:  # noqa: BLE001
            log.exception("history %s failed", asset.symbol)
            problems.append(f"Full history could not be refreshed: {type(e).__name__}")
        with contextlib.suppress(Exception):  # today's bar (the history is downloaded once a day)
            await self._ensure(asset, entry, p, "1d")
        rows = await asyncio.to_thread(self._load, asset.id, "1d", f"{p.name}:{entry.symbol}", since)
        return entry, p, dedupe_days(rows, day_tz(asset)), problems

    async def history_chart(self, asset: AssetRT, rng: str) -> dict[str, Any]:
        now, tz = utcnow(), day_tz(asset)
        since = {"YTD": (datetime(now.astimezone(tz).year, 1, 1, tzinfo=tz) - timedelta(days=1)).astimezone(timezone.utc),
                 "5Y": now - timedelta(days=round(5 * 365.25) + 4)}.get(rng, HISTORY_FLOOR)
        got = await self._history_rows(asset, since)
        if got is None:
            return {"range": rng, "candles": [], "problems": ["No full-history source for this asset."]}
        entry, p, rows, problems = got
        if rng == "YTD":  # from the last close of the previous year, so the first bar shows the year's start
            year = (rows[-1].ts.astimezone(tz).year if rows else now.year)
            rows = [r for r in rows if r.ts.astimezone(tz).year == year]
        if not rows:
            return {"range": rng, "candles": [], "problems": problems or [f"{p.label}: no history stored yet"]}
        bars = [(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows]
        tf = "1d"
        if rng == "MAX" and len(bars) > MAX_BARS:
            bars, tf = aggregate(bars, "1wk", tz), "1wk"
            if len(bars) > MAX_BARS:
                bars, tf = aggregate([(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows], "1mo", tz), "1mo"
        return {
            "range": rng, "timeframe": tf, "provider": p.name, "source": p.label, "symbol": entry.symbol,
            "note": entry.note, "is_proxy": entry.proxy, "is_unofficial": p.unofficial, "close_only": False,
            "daily_only_note": False, "newer_note": None, "history": True,
            "first_date": rows[0].ts.astimezone(tz).date().isoformat(), "daily_points": len(rows),
            "last_date": rows[-1].ts.astimezone(tz).date().isoformat(),
            "first_close": sig(rows[0].close),  # MAX summary starts here, like the performance table's "All history"  # weekly/monthly bars carry their first day
            "adjustment": adjustment_text(asset),
            "candles": [{"time": int(t.timestamp()), "open": sig(o), "high": sig(h), "low": sig(lo), "close": sig(c),
                         "volume": v or None} for t, o, h, lo, c, v in bars],
            "problems": problems,
        }

    async def performance(self, asset: AssetRT) -> dict[str, Any]:
        """Returns over standard periods, all-time high/low and the drop from the high, from the full daily history.
        Every number is arithmetic on stored closes; periods longer than the history are left out."""
        got = await self._history_rows(asset, HISTORY_FLOOR)
        if got is None:
            return {"available": False, "problems": ["No full-history source for this asset."]}
        entry, p, rows, problems = got
        if len(rows) < 2:
            return {"available": False, "problems": problems or ["Not enough history stored yet."]}
        return {"available": True, "source": p.label, "symbol": entry.symbol, "note": entry.note, "is_proxy": entry.proxy,
                "is_unofficial": p.unofficial, "problems": problems,
                "basis": ("Change of the daily USD closing price (days run 00:00-24:00 UTC)." if asset.schedule == mh.CRYPTO
                          else "Price return from daily closes, adjusted for stock splits, dividends not included."),
                **performance_table(rows, day_tz(asset))}

    # ------------------------------------------------------------------ sparklines
    async def refresh_sparklines(self, assets: list[AssetRT]) -> None:
        sem = asyncio.Semaphore(4)

        async def one(a: AssetRT) -> None:
            async with sem:
                daily_first = bool(a.candle_chain) and getattr(self.providers.get(a.candle_chain[0].provider), "close_only", False)
                try:
                    chart = await asyncio.wait_for(self.chart(a, "1M" if daily_first else "1D"), timeout=60)
                except Exception as e:  # noqa: BLE001
                    log.warning("sparkline %s failed: %s", a.symbol, type(e).__name__)
                    return
                closes = [c["close"] for c in chart.get("candles", [])]
                if closes:
                    self.spark[a.id] = {"points": closes[-120:], "source": chart.get("source"), "range": chart.get("range"),
                                        "is_proxy": chart.get("is_proxy"), "updated": utcnow().isoformat()}

        await asyncio.gather(*(one(a) for a in assets if a.enabled))

    def prune(self) -> int:
        """Delete old fine-grained candles (5m older than 10 days, 1h older than 400 days)."""
        now = utcnow()
        with self.db.session() as s:
            r1 = s.execute(delete(Candle).where(Candle.timeframe == "5m", Candle.ts < now - timedelta(days=10)))
            r2 = s.execute(delete(Candle).where(Candle.timeframe == "1h", Candle.ts < now - timedelta(days=400)))
        return (r1.rowcount or 0) + (r2.rowcount or 0)

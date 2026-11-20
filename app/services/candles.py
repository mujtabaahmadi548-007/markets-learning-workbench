"""Chart data: real candles from the asset's candle chain, stored in the `candles` table.

Ranges -> candle size:  1D -> 5m,  1W -> 1h,  1M -> 1h,  6M -> 1d,  1Y -> 1d.
Candles are fetched from a provider only when the stored ones are older than that provider's TTL
(and not at all while the market is closed and we already have data after the close), which keeps
free-plan credits low. One chart always shows ONE source (never mixed), named on the chart.
Sources with daily values only (FRED, US Treasury) are drawn as a line.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.session import Database
from app.models.tables import Candle
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
        if not candles:
            return
        with self.db.session() as s:
            for c in candles:
                stmt = sqlite_insert(Candle).values(asset_id=asset_id, timeframe=tf, ts=c.ts, open=c.open, high=c.high,
                                                    low=c.low, close=c.close, volume=c.volume, source=source)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["asset_id", "timeframe", "ts", "source"],
                    set_={"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume})
                s.execute(stmt)

    def _load(self, asset_id: int, tf: str, source: str, since: datetime) -> list[Candle]:
        with self.db.session() as s:
            return list(s.scalars(select(Candle).where(
                Candle.asset_id == asset_id, Candle.timeframe == tf, Candle.source == source, Candle.ts >= since,
            ).order_by(Candle.ts)))

    # ------------------------------------------------------------------ public
    async def chart(self, asset: AssetRT, rng: str) -> dict[str, Any]:
        if rng not in RANGES:
            rng = "1D"
        tf, lookback = RANGES[rng]
        problems: list[str] = []
        for entry in asset.candle_chain:
            p = self.providers.get(entry.provider)
            if p is None:
                continue
            if not p.configured:
                problems.append(f"{p.label}: no API key")
                continue
            h = self.health[p.name]
            if not h.symbol_available(f"candles:{entry.symbol}"):
                problems.append(f"{p.label}: cooling down after an error")
                continue
            tf_eff = "1d" if p.close_only else tf
            try:
                await self._ensure(asset, entry, p, tf_eff)
            except ProviderRateLimited as e:
                h.record_error(str(e), f"candles:{entry.symbol}", provider_wide=True)
                problems.append(str(e))
                continue
            except (ProviderError, asyncio.TimeoutError) as e:
                msg = str(e) or f"{p.label}: timed out"
                h.record_error(msg, f"candles:{entry.symbol}")
                problems.append(msg)
                continue
            except Exception as e:  # noqa: BLE001
                log.exception("candles %s %s failed", p.name, entry.symbol)
                problems.append(f"{p.label}: {type(e).__name__}")
                continue
            span = max(lookback, timedelta(days=31)) if p.close_only else lookback
            rows = await asyncio.to_thread(self._load, asset.id, tf_eff, f"{p.name}:{entry.symbol}", utcnow() - span - timedelta(days=4))
            rows = self._trim(rows, rng, tf_eff, asset.schedule, span)
            if not rows:
                problems.append(f"{p.label}: no candles in this range")
                continue
            return {
                "range": rng, "timeframe": tf_eff, "provider": p.name, "source": p.label, "symbol": entry.symbol,
                "note": entry.note, "is_proxy": entry.proxy, "is_unofficial": p.unofficial, "close_only": p.close_only,
                "daily_only_note": p.close_only and tf != "1d",
                "candles": [{"time": int(c.ts.timestamp()), "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                             "volume": c.volume} for c in rows],
                "problems": problems,
            }
        return {"range": rng, "candles": [], "problems": problems or ["No chart source is available for this asset."]}

    async def series(self, asset: AssetRT, tf: str, since: datetime) -> dict[str, Any]:
        """Candles of one source from `since` until now (first working source in the candle chain).
        Used for 'price since publish'. Daily-only sources return daily values."""
        problems: list[str] = []
        for entry in asset.candle_chain:
            p = self.providers.get(entry.provider)
            if p is None or not p.configured:
                continue
            h = self.health[p.name]
            if not h.symbol_available(f"candles:{entry.symbol}"):
                problems.append(f"{p.label}: cooling down after an error")
                continue
            tf_eff = "1d" if p.close_only else tf
            try:
                await self._ensure(asset, entry, p, tf_eff)
            except Exception as e:  # noqa: BLE001
                msg = str(e) or type(e).__name__
                h.record_error(msg, f"candles:{entry.symbol}")
                problems.append(msg)
                continue
            rows = await asyncio.to_thread(self._load, asset.id, tf_eff, f"{p.name}:{entry.symbol}", since)
            if not rows:
                problems.append(f"{p.label}: no candles for that period")
                continue
            return {"provider": p.name, "symbol": entry.symbol, "source": p.label, "note": entry.note, "is_proxy": entry.proxy,
                    "is_unofficial": p.unofficial, "close_only": p.close_only, "timeframe": tf_eff, "problems": problems,
                    "candles": [{"ts": c.ts, "open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in rows]}
        return {"candles": [], "problems": problems or ["No chart source for this asset"]}

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

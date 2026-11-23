"""Live price engine: streaming hub + poller + automatic fallback + persistence + browser broadcast.

How a price is chosen for each asset (every second):
1. Walk the asset's quote chain in order and take the first source that is usable:
   configured (key present), not switched off by you, not cooling down after errors, and - for
   streams - connected and actually delivering data while the market is open.
2. Streams (Binance, Gate.io, Finnhub) push updates as they happen. Polled sources are asked on
   their own timer. Finnhub's REST quote adds previous close and day range to its trade stream.
3. When a source fails, it cools down (5 s, 10 s, 20 s ... 5 min) and the next one takes over.
   The primary is retried automatically when its cooldown ends.
4. Official daily sources (FRED, US Treasury) publish after the close, often the next business day. When such a
   source comes first and an intraday source follows it in the chain (Yahoo for SPX, IXIC, US10Y), both are polled
   and the one with the newer US trading day is shown; on the same day the official source wins.
Every value shown comes from a source response (or simple arithmetic on those values).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.session import Database
from app.logging_setup import redact
from app.models.tables import AppSetting, PriceSnapshot, ProviderStatus, QuoteLatest
from app.providers.base import Provider, ProviderAuthError, ProviderError, ProviderRateLimited, QuoteData
from app.services import market_hours as mh
from app.services import status as st
from app.services.assets import AssetRT, ChainEntry
from app.services.provider_health import ProviderHealth
from app.timeutil import NEW_YORK, utcnow

log = logging.getLogger(__name__)

STREAM_GRACE = 20.0  # seconds a new stream connection gets before it counts as failed
STREAM_SILENCE = 60.0  # open market but no message for this long -> stream unhealthy for that symbol
SNAPSHOT_EVERY = 60.0


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@dataclass
class LiveQuote:
    asset_id: int
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    change_basis: str | None = None
    quote_currency: str | None = None
    source_timestamp: datetime | None = None
    received_at: datetime | None = None
    provider: str | None = None
    provider_symbol: str | None = None
    provider_label: str | None = None
    kind: str = "poll"
    entry_note: str = ""
    is_proxy: bool = False
    is_unofficial: bool = False
    fallback: bool = False
    newer_note: str | None = None  # why a later source is shown although the official daily one works
    poll_interval: float = 60.0
    expected_delay: float = 0.0
    max_age_days: int | None = None  # daily series published less often (e.g. weekly) get a longer STALE limit
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    error_message: str | None = None


class StreamRunner:
    """Keeps one WebSocket connection to a streaming provider for a set of symbols, reconnecting with backoff."""

    def __init__(self, provider: Provider, manager: "FeedManager") -> None:
        self.provider = provider
        self.manager = manager
        self.symbols: frozenset[str] = frozenset()
        self.task: asyncio.Task | None = None
        self.connected = False
        self.started_at = 0.0
        self.connected_at = 0.0
        self.failures = 0
        self.last_msg: dict[str, float] = {}
        self.last_error: str | None = None

    def set_symbols(self, symbols: set[str]) -> None:
        fs = frozenset(symbols)
        running = self.task is not None and not self.task.done()
        if fs == self.symbols and (running or not fs):
            return
        self.stop()
        self.symbols = fs
        if fs:
            self.started_at = time.monotonic()
            self.failures = 0
            self.task = asyncio.create_task(self._run(fs), name=f"stream-{self.provider.name}")
            log.info("Stream %s: subscribing %d symbols", self.provider.name, len(fs))

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        self.connected = False

    async def _run(self, symbols: frozenset[str]) -> None:
        backoff = 1.0
        health = self.manager.health[self.provider.name]
        while True:
            try:
                await self.provider.stream(set(symbols), self._on_update, self._on_connected)
                log.info("Stream %s closed by server; reconnecting", self.provider.name)  # e.g. Binance's 24 h limit
                backoff = 1.0
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as e:  # noqa: BLE001 - any failure -> reconnect with backoff
                msg = redact(f"{type(e).__name__}: {e}")[:200]
                self.last_error = msg
                self.failures += 1
                health.record_error(f"stream: {msg}")
                log.warning("Stream %s failed (%s); retry in %.0fs", self.provider.name, msg, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _on_connected(self) -> None:
        self.connected = True
        self.connected_at = time.monotonic()
        self.manager.health[self.provider.name].record_success()

    def _on_update(self, symbol: str, q: QuoteData) -> None:
        self.last_msg[symbol] = time.monotonic()
        self.manager.on_stream_quote(self.provider, symbol, q)

    def healthy_for(self, symbol: str, market_open: bool) -> bool:
        if symbol not in self.symbols:
            return False
        now = time.monotonic()
        if not self.connected:
            return self.failures == 0 and (now - self.started_at) < STREAM_GRACE
        if not market_open:
            return True
        ref = self.last_msg.get(symbol, self.connected_at)
        return (now - ref) < STREAM_SILENCE


class Broadcaster:
    """Open browser WebSocket connections."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def send_all(self, message: dict[str, Any]) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - client went away
                self.clients.discard(ws)


class FeedManager:
    def __init__(self, db: Database, providers: dict[str, Provider], assets: list[AssetRT]) -> None:
        self.db = db
        self.providers = providers
        self.assets: dict[int, AssetRT] = {a.id: a for a in assets if a.enabled}
        self.quotes: dict[int, LiveQuote] = {a.id: LiveQuote(asset_id=a.id) for a in self.assets.values()}
        self.health: dict[str, ProviderHealth] = {
            name: ProviderHealth(name=name, label=p.label, configured=p.configured, unofficial=p.unofficial)
            for name, p in providers.items()
        }
        self.runners: dict[str, StreamRunner] = {n: StreamRunner(p, self) for n, p in providers.items() if p.can_stream}
        self.broadcaster = Broadcaster()
        self.active: dict[int, tuple[int, ChainEntry] | None] = {}
        # Daily source + intraday backup: the entry polled alongside the shown one, and why the backup is shown.
        self.companion: dict[int, ChainEntry] = {}
        self.newer: dict[int, str] = {}
        # Last answer from each polled source, (asset, provider, symbol) -> (quote, monotonic time received).
        self.latest: dict[tuple[int, str, str], tuple[QuoteData, float]] = {}
        self.next_poll: dict[tuple[int, str], float] = {}
        self.polling: set[tuple[int, str]] = set()
        self.dirty: set[int] = set()
        self.last_sent: dict[int, tuple] = {}
        self.ref24: dict[int, tuple[str, float, datetime]] = {}  # asset -> (source key, price, ts) ~24 h ago
        self.last_snap_ts: dict[int, datetime] = {}
        self.tasks: list[asyncio.Task] = []
        self._poll_tasks: set[asyncio.Task] = set()
        self.on_update: list = []  # callbacks(asset_id) after every applied price update (alerts)
        self._session_cache: dict[str, tuple[float, mh.SessionInfo]] = {}
        # Per-source fetch diagnostics, (asset, provider, symbol) -> last attempt / last success / last error, so a
        # failing source is visible (asset JSON, website quotes file, build log) instead of silently freezing a price.
        self.diag: dict[tuple[int, str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await asyncio.to_thread(self._load_state)
        for name, coro in (("supervisor", self._supervisor_loop()), ("flush", self._flush_loop()),
                           ("broadcast", self._broadcast_loop()), ("snapshots", self._snapshot_loop()),
                           ("ref24", self._ref24_loop()), ("provider-status", self._provider_status_loop())):
            self.tasks.append(asyncio.create_task(self._guard(name, coro), name=f"feed-{name}"))
        log.info("Feed manager started for %d assets", len(self.assets))

    async def stop(self) -> None:
        for r in self.runners.values():
            r.stop()
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.tasks.clear()
        with contextlib.suppress(Exception):
            rows = self._quote_rows(set(self.quotes))
            await asyncio.to_thread(self._write_quote_rows, rows)

    async def _guard(self, name: str, coro) -> None:  # noqa: ANN001
        """Keep a background loop alive: log any crash and restart it (the app must keep running)."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Background loop %s crashed; restarting in 5 s", name)
            await asyncio.sleep(5)
            loops = {"supervisor": self._supervisor_loop, "flush": self._flush_loop, "broadcast": self._broadcast_loop,
                     "snapshots": self._snapshot_loop, "ref24": self._ref24_loop, "provider-status": self._provider_status_loop}
            self.tasks.append(asyncio.create_task(self._guard(name, loops[name]()), name=f"feed-{name}"))

    def _load_state(self) -> None:
        """Restore the last stored quotes (shown with their real age) and providers you switched off."""
        with self.db.session() as s:
            row = s.get(AppSetting, "disabled_providers")
            for name in (row.value if row else []) or []:
                if name in self.health:
                    self.health[name].manually_disabled = True
            for q in s.scalars(select(QuoteLatest)):
                lq = self.quotes.get(q.asset_id)
                if lq is None or q.price is None or not q.source:
                    continue
                provider, _, symbol = q.source.partition(":")
                p = self.providers.get(provider)
                lq.price, lq.change, lq.change_pct = q.price, q.change, q.change_pct
                lq.change_basis = q.change_basis
                if lq.change_basis is None:  # stored before schema v3: don't show a change we can't label
                    lq.change = lq.change_pct = None
                lq.prev_close, lq.day_high, lq.day_low, lq.volume = q.prev_close, q.day_high, q.day_low, q.volume
                lq.quote_currency, lq.source_timestamp, lq.received_at = q.quote_currency, q.source_timestamp, q.received_at
                lq.provider, lq.provider_symbol = provider, symbol
                lq.provider_label = p.label if p else provider
                lq.kind = p.kind if p else "poll"
                lq.poll_interval = p.poll_interval if p else 60.0
                lq.expected_delay = p.expected_delay if p else 0.0
                lq.is_proxy, lq.is_unofficial = q.is_proxy, q.is_unofficial
                lq.last_success_at = q.last_success_at
                entry = self._entry_for(self.assets[q.asset_id], provider, symbol)
                lq.entry_note = entry.note if entry else ""
                lq.max_age_days = entry.max_age_days if entry else None

    # ------------------------------------------------------------------ helpers
    def _entry_for(self, asset: AssetRT, provider: str, symbol: str) -> ChainEntry | None:
        for e in asset.quote_chain:
            if e.provider == provider and e.symbol == symbol:
                return e
        return None

    def session(self, schedule: str) -> mh.SessionInfo:
        cached = self._session_cache.get(schedule)
        if cached and time.monotonic() - cached[0] < 20:
            return cached[1]
        info = mh.session_info(schedule, utcnow())
        self._session_cache[schedule] = (time.monotonic(), info)
        return info

    def _configured_entries(self, asset: AssetRT) -> list[tuple[int, ChainEntry, Provider]]:
        out = []
        for i, e in enumerate(asset.quote_chain):
            p = self.providers.get(e.provider)
            if p is not None and p.configured and p.serves(e.symbol):
                out.append((i, e, p))
        return out

    def _first_usable(self, asset: AssetRT, market_open: bool) -> tuple[int, ChainEntry] | None:
        for i, e, p in self._configured_entries(asset):
            if not self.health[p.name].symbol_available(e.symbol):
                continue
            if p.can_stream:
                if self.runners[p.name].healthy_for(e.symbol, market_open):
                    return i, e
                continue
            return i, e
        return None

    def _intraday_after(self, asset: AssetRT, idx: int) -> tuple[int, ChainEntry] | None:
        """The first usable polled (not daily, not streaming) source after position idx in the chain."""
        for j, e, p in self._configured_entries(asset):
            if j > idx and p.kind != "daily" and not p.can_stream and self.health[p.name].symbol_available(e.symbol):
                return j, e
        return None

    def _choose(self, asset: AssetRT, market_open: bool) -> tuple[int, ChainEntry] | None:
        """The source to show. A daily official source (FRED, US Treasury) gives way to the intraday source after it
        when that one already has a newer US trading day (e.g. Friday's close while FRED still ends on Thursday)."""
        first = self._first_usable(asset, market_open)
        self.companion.pop(asset.id, None)
        self.newer.pop(asset.id, None)
        if first is None or self.providers[first[1].provider].kind != "daily":
            return first
        alt = self._intraday_after(asset, first[0])
        if alt is None:
            return first
        daily_e, alt_e = first[1], alt[1]
        got_alt = self.latest.get((asset.id, alt_e.provider, alt_e.symbol))
        got_daily = self.latest.get((asset.id, daily_e.provider, daily_e.symbol))
        daily_ts = got_daily[0].source_timestamp if got_daily else None
        if daily_ts is None:
            lq = self.quotes[asset.id]
            if lq.provider == daily_e.provider and lq.provider_symbol == daily_e.symbol:
                daily_ts = lq.source_timestamp  # restored from the database at start
        # Daily values are stamped 00:00 UTC of their date; intraday times are compared by their New York date.
        if got_alt is None or daily_ts is None or got_alt[0].source_timestamp.astimezone(NEW_YORK).date() <= daily_ts.date():
            self.companion[asset.id] = alt_e
            return first
        dp, ap = self.providers[daily_e.provider], self.providers[alt_e.provider]
        d, a = daily_ts.date(), got_alt[0].source_timestamp.astimezone(NEW_YORK).date()
        self.newer[asset.id] = (f"{dp.label} has published values up to {d.day} {d:%b %Y} so far (it publishes after the "
                                f"close, often the next business day). {ap.label} already has {a.day} {a:%b %Y}, "
                                "so its newer price is shown.")
        self.companion[asset.id] = daily_e
        return alt

    # ------------------------------------------------------------------ supervisor
    async def _supervisor_loop(self) -> None:
        while True:
            self._tick()
            await asyncio.sleep(1.0)

    def _tick(self) -> None:
        # 1) which stream symbols are wanted: the first usable stream in each chain, plus lower streams
        #    only while every stream above them is unhealthy.
        desired: dict[str, set[str]] = {n: set() for n in self.runners}
        for asset in self.assets.values():
            mo = self.session(asset.schedule).open
            for _, e, p in self._configured_entries(asset):
                if not self.health[p.name].usable():
                    continue
                if not p.can_stream:
                    break
                desired[p.name].add(e.symbol)
                r = self.runners[p.name]
                if e.symbol not in r.symbols or r.healthy_for(e.symbol, mo):
                    break
        for name, runner in self.runners.items():
            runner.set_symbols(desired[name])

        # 2) choose the active source per asset and start polls that are due
        now = time.monotonic()
        for asset in self.assets.values():
            mo = self.session(asset.schedule).open
            prev = self.active.get(asset.id)
            self._hold_off_silent_stream(asset, prev, mo)
            choice = self._choose(asset, mo)
            self.active[asset.id] = choice
            if (prev and prev[1]) != (choice and choice[1]):
                if choice:
                    log.info("%s: price source -> %s %s", asset.symbol, choice[1].provider, choice[1].symbol)
                    p = self.providers[choice[1].provider]
                    got = self.latest.get((asset.id, p.name, choice[1].symbol))
                    if got is not None and not p.can_stream and now - got[1] < p.poll_interval:
                        self._apply(asset, choice[1], p, got[0], "full")  # answered recently: show it right away
                    else:
                        self.next_poll[(asset.id, choice[1].provider)] = 0.0
                else:
                    log.warning("%s: no usable price source right now", asset.symbol)
                self.dirty.add(asset.id)
            if not choice:
                continue
            _, entry = choice
            p = self.providers[entry.provider]
            needs_poll = not p.can_stream or (p.name == "finnhub" and ":" not in entry.symbol)
            if needs_poll:
                self._start_poll(asset, entry, p, "stats" if p.can_stream else "full", now)
            other = self.companion.get(asset.id)
            if other is not None:  # daily source + intraday backup: keep both answers current
                self._start_poll(asset, other, self.providers[other.provider], "full", now)

    def _start_poll(self, asset: AssetRT, entry: ChainEntry, p: Provider, mode: str, now: float) -> None:
        key = (asset.id, p.name)
        if key in self.polling or now < self.next_poll.get(key, 0.0):
            return
        self.polling.add(key)
        task = asyncio.create_task(self._poll(asset, entry, p, mode), name=f"poll-{asset.symbol}-{p.name}")
        self._poll_tasks.add(task)
        task.add_done_callback(self._poll_tasks.discard)

    def _is_active(self, asset_id: int, entry: ChainEntry) -> bool:
        act = self.active.get(asset_id)
        return act is not None and act[1] == entry

    def _hold_off_silent_stream(self, asset: AssetRT, prev: tuple[int, ChainEntry] | None, market_open: bool) -> None:
        """A connected stream that went quiet for one symbol is skipped for 5 min (doubling up to 1 h), so the
        price does not flip back and forth between two different instruments (e.g. a CFD and a futures contract)."""
        if not prev:
            return
        entry = prev[1]
        p = self.providers.get(entry.provider)
        runner = self.runners.get(entry.provider)
        if p is None or runner is None or not runner.connected or not market_open:
            return
        if entry.symbol in runner.symbols and not runner.healthy_for(entry.symbol, market_open):
            h = self.health[p.name]
            n = h.symbol_fail.get(entry.symbol, 0) + 1
            h.symbol_fail[entry.symbol] = n
            h.symbol_cooldown_until[entry.symbol] = time.monotonic() + min(3600.0, 300.0 * 2 ** (n - 1))
            runner.last_msg.pop(entry.symbol, None)
            log.info("%s: %s sent no update for %s for 60 s; using the next source for %d min",
                     asset.symbol, p.label, entry.symbol, int(min(3600.0, 300.0 * 2 ** (n - 1)) // 60))

    async def _poll(self, asset: AssetRT, entry: ChainEntry, p: Provider, mode: str) -> None:
        """Ask one source. Its answer is shown only if it is still the asset's chosen source when the answer
        arrives (a daily source's companion is polled too, but only compared)."""
        key = (asset.id, p.name)
        health = self.health[p.name]
        d = self._diag(asset.id, p.name, entry.symbol)
        d["last_attempted_at"] = utcnow()
        try:
            q = await asyncio.wait_for(p.get_quote(entry.symbol), timeout=30)
            self._check_session(asset, entry, p, q)
            health.record_success(entry.symbol)
            d["last_succeeded_at"], d["error"], d["source_timestamp"] = utcnow(), None, q.source_timestamp
            self.latest[(asset.id, p.name, entry.symbol)] = (q, time.monotonic())
            if self._is_active(asset.id, entry):
                self._apply(asset, entry, p, q, mode)
        except (ProviderRateLimited, ProviderAuthError) as e:  # affects every symbol of this source
            health.record_error(str(e), entry.symbol, provider_wide=True)
            d["error"] = str(e)
            self._note_error(asset, p, str(e), entry)
        except ProviderError as e:
            health.record_error(str(e), entry.symbol)
            d["error"] = str(e)
            self._note_error(asset, p, str(e), entry)
        except asyncio.TimeoutError:
            health.record_error(f"{p.label}: no answer within 30 s", entry.symbol)
            d["error"] = f"{p.label}: no answer within 30 s"
            self._note_error(asset, p, "no answer within 30 s", entry)
        except Exception as e:  # noqa: BLE001
            msg = redact(f"{type(e).__name__}: {e}")[:200]
            log.exception("poll %s %s failed", p.name, entry.symbol)
            health.record_error(f"{p.label}: {msg}", entry.symbol)
            d["error"] = f"{p.label}: {msg}"
            self._note_error(asset, p, msg, entry)
        finally:
            self.polling.discard(key)
            self.next_poll[key] = time.monotonic() + p.poll_interval

    def _diag(self, asset_id: int, provider: str, symbol: str) -> dict[str, Any]:
        k = (asset_id, provider, symbol)
        if k not in self.diag:
            self.diag[k] = {"provider": provider, "symbol": symbol, "last_attempted_at": None,
                            "last_succeeded_at": None, "error": None, "source_timestamp": None}
        return self.diag[k]

    def _check_session(self, asset: AssetRT, entry: ChainEntry, p: Provider, q: QuoteData) -> None:
        """A polled quote from before the session that is open now (e.g. Friday's close on Monday afternoon) counts
        as a failure when a later intraday source exists in the chain, so that source takes over instead of the
        price sitting on an old session."""
        if p.kind == "daily":
            return
        sess = self.session(asset.schedule)
        if not sess.open or sess.opened_at is None or q.source_timestamp >= sess.opened_at:
            return
        if (utcnow() - sess.opened_at).total_seconds() <= st.SESSION_GRACE:
            return
        idx = asset.quote_chain.index(entry)
        if not any(j > idx and p2.kind != "daily" for j, _, p2 in self._configured_entries(asset)):
            return
        when = q.source_timestamp.astimezone(NEW_YORK)
        raise ProviderError(f"{p.label}: quote is from {when:%a %d %b %H:%M} New York time, before the session "
                            "that is open now; using the next source")

    def _note_error(self, asset: AssetRT, p: Provider, message: str, entry: ChainEntry | None = None) -> None:
        if entry is not None and not self._is_active(asset.id, entry):
            return  # a source that is not shown failed: its health records it, the shown price is unaffected
        lq = self.quotes[asset.id]
        lq.last_error_at = utcnow()
        lq.error_message = message if message.startswith(p.label) else f"{p.label}: {message}"
        self.dirty.add(asset.id)

    # ------------------------------------------------------------------ applying data
    def on_stream_quote(self, p: Provider, symbol: str, q: QuoteData) -> None:
        for asset in self.assets.values():
            choice = self.active.get(asset.id)
            if choice and choice[1].provider == p.name and choice[1].symbol == symbol:
                d = self._diag(asset.id, p.name, symbol)
                d["last_attempted_at"] = d["last_succeeded_at"] = utcnow()
                d["error"], d["source_timestamp"] = None, q.source_timestamp
                self._apply(asset, choice[1], p, q, "trade" if p.name == "finnhub" else "full")

    def _apply(self, asset: AssetRT, entry: ChainEntry, p: Provider, q: QuoteData, mode: str) -> None:
        lq = self.quotes[asset.id]
        same_source = lq.provider == p.name and lq.provider_symbol == entry.symbol
        # US stocks: outside the regular session show the official close, not pre/after-hours trades.
        stock_closed = asset.schedule == mh.US_EQUITY and not self.session(asset.schedule).open
        if mode == "trade" and stock_closed:
            return
        if mode == "stats" and stock_closed:
            lq.source_timestamp = None  # let the REST quote's regular-session close replace any extended-hours price
        if mode == "full":
            lq.price, lq.change, lq.change_pct, lq.prev_close = q.price, q.change, q.change_pct, q.prev_close
            lq.day_high, lq.day_low, lq.volume = q.day_high, q.day_low, q.volume
            lq.source_timestamp = q.source_timestamp
            lq.quote_currency, lq.change_basis = q.quote_currency, q.change_basis
        elif mode == "trade":  # price-only update from a trade stream
            if not same_source:
                lq.prev_close = lq.day_high = lq.day_low = lq.volume = None
                lq.change_basis = "prev_close"
                lq.quote_currency = "USD"
            if lq.source_timestamp is None or not same_source or q.source_timestamp >= lq.source_timestamp:
                lq.price, lq.source_timestamp = q.price, q.source_timestamp
                if lq.day_high is not None and q.price > lq.day_high:
                    lq.day_high = q.price
                if lq.day_low is not None and q.price < lq.day_low:
                    lq.day_low = q.price
        else:  # "stats": REST quote alongside a trade stream
            lq.prev_close, lq.day_high, lq.day_low = q.prev_close, q.day_high, q.day_low
            lq.volume = q.volume if q.volume is not None else lq.volume
            lq.change_basis, lq.quote_currency = q.change_basis, q.quote_currency
            if not same_source or lq.source_timestamp is None or q.source_timestamp > lq.source_timestamp:
                lq.price, lq.source_timestamp = q.price, q.source_timestamp
        if mode in ("trade", "stats"):
            if lq.prev_close:
                lq.change = lq.price - lq.prev_close
                lq.change_pct = lq.change / lq.prev_close * 100
            else:
                lq.change = lq.change_pct = None
        if lq.change is None:
            self._apply_ref24(asset.id, lq, f"{p.name}:{entry.symbol}")

        # "fallback" = a source further down the chain is in use (also when you switched the main one off).
        configured = self._configured_entries(asset)
        first_idx = configured[0][0] if configured else None
        idx = asset.quote_chain.index(entry)
        lq.provider, lq.provider_symbol, lq.provider_label = p.name, entry.symbol, p.label
        lq.kind, lq.poll_interval, lq.expected_delay = p.kind, p.poll_interval, p.expected_delay
        lq.entry_note, lq.is_proxy, lq.is_unofficial = entry.note, entry.proxy, p.unofficial
        lq.max_age_days = entry.max_age_days
        # A newer source chosen over a working daily one is not a fallback (nothing failed); say why it is shown.
        lq.newer_note = self.newer.get(asset.id) if self._is_active(asset.id, entry) else None
        lq.fallback = first_idx is not None and idx > first_idx and lq.newer_note is None
        now = utcnow()
        lq.received_at = lq.last_success_at = now
        self.dirty.add(asset.id)
        for hook in self.on_update:
            try:
                hook(asset.id)
            except Exception:  # noqa: BLE001 - a hook must never break the price engine
                log.exception("price update hook failed")

    def _apply_ref24(self, asset_id: int, lq: LiveQuote, source_key: str) -> None:
        """Change over 24 h from a price this app recorded itself from the SAME source ~24 h earlier."""
        ref = self.ref24.get(asset_id)
        if ref and ref[0] == source_key and lq.price is not None and ref[1]:
            lq.prev_close = ref[1]
            lq.change = lq.price - ref[1]
            lq.change_pct = lq.change / ref[1] * 100
            lq.change_basis = "24h_recorded"
        else:
            lq.prev_close = lq.prev_close if lq.change_basis not in (None, "24h_recorded") else None
            lq.change_basis = None

    # ------------------------------------------------------------------ views
    def view(self, asset_id: int) -> dict[str, Any]:
        asset = self.assets[asset_id]
        lq = self.quotes[asset_id]
        sess = self.session(asset.schedule)
        now = utcnow()
        configured = self._configured_entries(asset)
        unavailable = None
        if not configured:
            unavailable = asset.unavailable_reason or self._missing_keys_text(asset)
        res = st.compute_status(st.StatusInput(
            now=now, has_price=lq.price is not None, kind=lq.kind, market_open=sess.open,
            source_timestamp=lq.source_timestamp, last_success_at=lq.last_success_at, last_error_at=lq.last_error_at,
            error_message=lq.error_message, poll_interval=lq.poll_interval, expected_delay=lq.expected_delay,
            unavailable_reason=unavailable, daily_max_age_days=lq.max_age_days or st.DAILY_STALE_DAYS,
            phase=sess.phase, session_opened_at=sess.opened_at,
        ))
        cadence = "intraday"
        if lq.kind == "daily":
            cadence = "weekly" if (lq.max_age_days or 0) >= st.WEEKLY_MIN_DAYS else "daily"
        diagnostics = [
            {k: (iso(v) if isinstance(v, datetime) else v) for k, v in self.diag[(asset_id, e.provider, e.symbol)].items()}
            for e in asset.quote_chain if (asset_id, e.provider, e.symbol) in self.diag
        ]
        return {
            "id": asset.id, "symbol": asset.symbol, "name": asset.name, "asset_class": asset.asset_class,
            "decimals": asset.decimals, "currency": asset.currency,
            "price": lq.price, "change": lq.change, "change_pct": lq.change_pct, "change_basis": lq.change_basis,
            "prev_close": lq.prev_close, "day_high": lq.day_high, "day_low": lq.day_low, "volume": lq.volume,
            "quote_currency": lq.quote_currency,
            "provider": lq.provider, "provider_symbol": lq.provider_symbol, "source": lq.provider_label,
            "source_note": lq.entry_note, "kind": lq.kind, "is_proxy": lq.is_proxy, "is_unofficial": lq.is_unofficial,
            "fallback": lq.fallback, "newer_note": lq.newer_note, "cadence": cadence, "max_age_days": lq.max_age_days,
            "diagnostics": diagnostics,
            "status": res.status, "status_label": res.label, "status_detail": res.detail,
            "source_timestamp": iso(lq.source_timestamp), "received_at": iso(lq.received_at),
            "last_success_at": iso(lq.last_success_at), "last_error_at": iso(lq.last_error_at),
            "error_message": lq.error_message if (lq.last_error_at and (not lq.last_success_at or lq.last_error_at > lq.last_success_at)) else None,
            "market": {"open": sess.open, "reason": sess.reason, "last_close": iso(sess.last_close),
                       "next_open": iso(sess.next_open), "schedule": asset.schedule, "phase": sess.phase},
        }

    def _missing_keys_text(self, asset: AssetRT) -> str:
        keys = {"finnhub": "FINNHUB_API_KEY", "twelvedata": "TWELVE_DATA_API_KEY"}
        missing = sorted({keys[e.provider] for e in asset.quote_chain if e.provider in keys})
        if missing:
            return "Needs " + " or ".join(missing) + " in .env"
        return "No source is configured for this asset"

    def views(self) -> list[dict[str, Any]]:
        return [self.view(a.id) for a in sorted(self.assets.values(), key=lambda a: (a.sort_order, a.id))]

    # ------------------------------------------------------------------ loops: flush, broadcast, snapshots
    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.dirty:
                ids, self.dirty = set(self.dirty), set()
                rows = self._quote_rows(ids)  # built here (event loop), written in a worker thread
                await asyncio.to_thread(self._write_quote_rows, rows)

    def _flush_quotes(self, ids: set[int]) -> None:
        self._write_quote_rows(self._quote_rows(ids))

    def _quote_rows(self, ids: set[int]) -> list[dict[str, Any]]:
        rows = []
        for aid in ids:
            lq = self.quotes.get(aid)
            if lq is None:
                continue
            v = self.view(aid)
            rows.append(dict(
                asset_id=aid, price=lq.price, change=lq.change, change_pct=lq.change_pct, change_basis=lq.change_basis,
                prev_close=lq.prev_close,
                day_high=lq.day_high, day_low=lq.day_low, volume=lq.volume,
                source=f"{lq.provider}:{lq.provider_symbol}" if lq.provider else None,
                status=v["status"], delay_seconds=int(lq.expected_delay) if lq.kind != "stream" else 0,
                quote_currency=lq.quote_currency, is_proxy=lq.is_proxy, is_unofficial=lq.is_unofficial,
                error_message=v["error_message"], source_timestamp=lq.source_timestamp, received_at=lq.received_at,
                last_success_at=lq.last_success_at,
            ))
        return rows

    def _write_quote_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self.db.session() as s:
            for r in rows:
                stmt = sqlite_insert(QuoteLatest).values(**r)
                stmt = stmt.on_conflict_do_update(index_elements=["asset_id"], set_={k: v for k, v in r.items() if k != "asset_id"})
                s.execute(stmt)

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if not self.broadcaster.clients:
                continue
            changed = []
            for v in self.views():
                sig = (v["price"], v["change"], v["status"], v["status_label"], v["source_timestamp"], v["provider"],
                       v["error_message"], v["day_high"], v["day_low"], v["market"]["open"])
                if self.last_sent.get(v["id"]) != sig:
                    self.last_sent[v["id"]] = sig
                    changed.append(v)
            if changed:
                await self.broadcaster.send_all({"type": "quotes", "server_time": utcnow().isoformat(), "quotes": changed})

    async def _snapshot_loop(self) -> None:
        """Every minute, store each fresh intraday price (for history and 'price since article')."""
        while True:
            await asyncio.sleep(SNAPSHOT_EVERY)
            rows = []
            for aid, lq in self.quotes.items():
                if lq.price is None or lq.source_timestamp is None or lq.kind == "daily" or not lq.provider:
                    continue
                if self.last_snap_ts.get(aid) == lq.source_timestamp:
                    continue
                self.last_snap_ts[aid] = lq.source_timestamp
                rows.append(PriceSnapshot(asset_id=aid, price=lq.price, source=f"{lq.provider}:{lq.provider_symbol}",
                                          source_timestamp=lq.source_timestamp, received_at=utcnow()))
            if rows:
                await asyncio.to_thread(self._save_rows, rows)

    def _save_rows(self, rows: list) -> None:  # noqa: ANN001
        with self.db.session() as s:
            s.add_all(rows)

    async def _ref24_loop(self) -> None:
        """Every 5 min, look up the price this app recorded ~24 h ago (same source) for assets without a change."""
        while True:
            await asyncio.sleep(15 if not self.ref24 else 300)
            wanted = {aid: f"{lq.provider}:{lq.provider_symbol}" for aid, lq in self.quotes.items()
                      if lq.provider and lq.change_basis in (None, "24h_recorded")}
            if wanted:
                found = await asyncio.to_thread(self._find_ref24, wanted)
                self.ref24.update(found)

    def _find_ref24(self, wanted: dict[int, str]) -> dict[int, tuple[str, float, datetime]]:
        target = utcnow() - timedelta(hours=24)
        out = {}
        with self.db.session() as s:
            for aid, source in wanted.items():
                row = s.scalars(
                    select(PriceSnapshot).where(
                        PriceSnapshot.asset_id == aid, PriceSnapshot.source == source,
                        PriceSnapshot.source_timestamp.between(target - timedelta(minutes=20), target + timedelta(minutes=20)),
                    ).order_by(PriceSnapshot.source_timestamp.desc()).limit(1)
                ).first()
                if row:
                    out[aid] = (source, row.price, row.source_timestamp)
        return out

    async def _provider_status_loop(self) -> None:
        while True:
            await asyncio.sleep(10.0)
            await asyncio.to_thread(self._save_provider_status)

    def _save_provider_status(self) -> None:
        now = utcnow()
        with self.db.session() as s:
            for h in self.health.values():
                row = s.get(ProviderStatus, h.name) or ProviderStatus(provider=h.name)
                row.status, row.last_success, row.last_error = h.status, h.last_success, h.last_error
                row.error_message, row.error_count, row.updated_at = h.error_message, h.error_count, now
                s.merge(row)

    # ------------------------------------------------------------------ watchlist changes (Settings page)
    def set_assets(self, assets: list[AssetRT]) -> None:
        """Apply an edited watchlist without a restart: new assets start, removed ones stop."""
        enabled = {a.id: a for a in assets if a.enabled}
        for aid in list(self.assets):
            if aid not in enabled:
                self.assets.pop(aid, None)
                self.quotes.pop(aid, None)
                self.active.pop(aid, None)
                self.last_sent.pop(aid, None)
                self.companion.pop(aid, None)
                self.newer.pop(aid, None)
                for k in [k for k in self.latest if k[0] == aid]:
                    self.latest.pop(k, None)
        for aid, a in enabled.items():
            self.assets[aid] = a
            self.quotes.setdefault(aid, LiveQuote(asset_id=aid))
        for name, p in self.providers.items():
            if p.can_stream and name not in self.runners:
                self.runners[name] = StreamRunner(p, self)

    def change_pct_for(self, symbol: str) -> float:
        for a in self.assets.values():
            if a.symbol == symbol:
                v = self.quotes[a.id]
                return v.change_pct or 0.0
        return 0.0

    # ------------------------------------------------------------------ manual switch (testing fallback)
    def set_provider_enabled(self, name: str, enabled: bool) -> None:
        if name not in self.health:
            raise KeyError(name)
        self.health[name].manually_disabled = not enabled
        disabled = sorted(n for n, h in self.health.items() if h.manually_disabled)
        with self.db.session() as s:
            row = s.get(AppSetting, "disabled_providers")
            if row is None:
                s.add(AppSetting(key="disabled_providers", value=disabled, updated_at=utcnow()))
            else:
                row.value, row.updated_at = disabled, utcnow()
        log.info("Provider %s %s by user", name, "enabled" if enabled else "disabled")

    def provider_views(self) -> list[dict[str, Any]]:
        out = []
        for name, h in self.health.items():
            r = self.runners.get(name)
            out.append({
                "provider": name, "label": h.label, "status": h.status, "configured": h.configured,
                "unofficial": h.unofficial, "manually_disabled": h.manually_disabled,
                "last_success": iso(h.last_success), "last_error": iso(h.last_error), "error_message": h.error_message,
                "error_count": h.error_count,
                "stream": None if r is None else {"connected": r.connected, "symbols": len(r.symbols)},
                "assets_using": sorted(a.symbol for aid, a in self.assets.items()
                                       if (self.active.get(aid) or (None, None))[1] is not None
                                       and self.active[aid][1].provider == name),
            })
        return out

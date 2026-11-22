"""Automatic fallback between providers (fake providers; no network)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrations import init_db
from app.db.session import Database
from app.providers.base import Provider, ProviderError, QuoteData
from app.services import market_hours as mh
from app.services.assets import AssetRT, ChainEntry
from app.services.feeds import FeedManager
from app.timeutil import utcnow


class FakePoll(Provider):
    kind = "poll"
    poll_interval = 0.01

    def __init__(self, name: str, price: float) -> None:
        super().__init__(http=None)  # type: ignore[arg-type]
        self.name, self.label, self.price, self.fail, self.calls = name, f"Fake {name}", price, False, 0

    async def get_quote(self, symbol: str) -> QuoteData:
        self.calls += 1
        if self.fail:
            raise ProviderError(f"{self.label}: simulated outage")
        return QuoteData(price=self.price, source_timestamp=utcnow(), change=1.0, change_pct=0.5, prev_close=self.price - 1)


class FakeStream(Provider):
    kind = "stream"

    def __init__(self, name: str) -> None:
        super().__init__(http=None)  # type: ignore[arg-type]
        self.name, self.label = name, f"Fake {name}"

    @property
    def can_stream(self) -> bool:
        return True

    async def stream(self, symbols, on_update, on_connected) -> None:  # noqa: ANN001
        raise ProviderError("simulated: cannot connect")


def make(tmp_path: Path, chain: list[ChainEntry], providers: dict[str, Provider], schedule: str = mh.CRYPTO) -> FeedManager:
    db = Database(tmp_path / "f.db")
    init_db(db)
    asset = AssetRT(id=1, symbol="TEST", name="Test", asset_class="crypto", schedule=schedule, currency="USD",
                    decimals=2, enabled=True, sort_order=0, quote_chain=chain)
    return FeedManager(db, providers, [asset])


async def settle(mgr: FeedManager, rounds: int = 3) -> None:
    for _ in range(rounds):
        mgr._tick()
        await asyncio.sleep(0.05)


def test_primary_failure_switches_to_backup_and_back(tmp_path: Path) -> None:
    async def run() -> None:
        a, b = FakePoll("a", 100.0), FakePoll("b", 101.0)
        mgr = make(tmp_path, [ChainEntry("a", "X"), ChainEntry("b", "Y")], {"a": a, "b": b})
        await settle(mgr)
        q = mgr.view(1)
        assert q["provider"] == "a" and q["price"] == 100.0 and q["fallback"] is False

        a.fail = True
        await settle(mgr, 5)
        q = mgr.view(1)
        assert q["provider"] == "b" and q["price"] == 101.0 and q["fallback"] is True
        assert mgr.health["a"].consecutive_errors >= 1

        a.fail = False
        mgr.health["a"].symbol_cooldown_until.clear()  # cooldown over
        await settle(mgr, 4)
        assert mgr.view(1)["provider"] == "a"

    asyncio.run(run())


def test_manual_switch_off_triggers_fallback(tmp_path: Path) -> None:
    async def run() -> None:
        a, b = FakePoll("a", 100.0), FakePoll("b", 101.0)
        mgr = make(tmp_path, [ChainEntry("a", "X"), ChainEntry("b", "Y")], {"a": a, "b": b})
        await settle(mgr)
        mgr.set_provider_enabled("a", False)
        await settle(mgr)
        assert mgr.view(1)["provider"] == "b"
        assert "a" in (mgr.provider_views()[0]["provider"],) and mgr.health["a"].status == "DISABLED"
        mgr.set_provider_enabled("a", True)
        await settle(mgr)
        assert mgr.view(1)["provider"] == "a"

    asyncio.run(run())


def test_broken_stream_falls_back_to_poll(tmp_path: Path) -> None:
    async def run() -> None:
        s, b = FakeStream("s"), FakePoll("b", 55.0)
        mgr = make(tmp_path, [ChainEntry("s", "S"), ChainEntry("b", "Y")], {"s": s, "b": b})
        await settle(mgr, 2)
        assert mgr.runners["s"].failures >= 1  # stream tried and failed
        await settle(mgr, 3)
        q = mgr.view(1)
        assert q["provider"] == "b" and q["price"] == 55.0
        for r in mgr.runners.values():
            r.stop()

    asyncio.run(run())


def test_all_sources_failing_shows_error_not_fake_price(tmp_path: Path) -> None:
    async def run() -> None:
        a = FakePoll("a", 100.0)
        a.fail = True
        mgr = make(tmp_path, [ChainEntry("a", "X")], {"a": a})
        await settle(mgr, 3)
        q = mgr.view(1)
        assert q["price"] is None and q["status"] == "ERROR" and "simulated outage" in q["status_detail"]

    asyncio.run(run())


class FakeAt(Provider):
    """A polled source whose answer has a fixed time (daily sources: 00:00 UTC of the value's date)."""

    def __init__(self, name: str, kind: str, price: float, ts: datetime) -> None:
        super().__init__(http=None)  # type: ignore[arg-type]
        self.name, self.label, self.kind, self.price, self.ts, self.fail = name, f"Fake {name}", kind, price, ts, False
        self.poll_interval = 0.01

    async def get_quote(self, symbol: str) -> QuoteData:
        if self.fail:
            raise ProviderError(f"{self.label}: simulated outage")
        return QuoteData(price=self.price, source_timestamp=self.ts, change=-1.0, change_pct=-0.1, prev_close=self.price + 1)


THU = datetime(2026, 11, 19, tzinfo=timezone.utc)  # FRED-style stamp for Thursday's value
FRI = datetime(2026, 11, 20, tzinfo=timezone.utc)
FRI_CLOSE = datetime(2026, 11, 20, 21, 35, tzinfo=timezone.utc)  # Friday 4:35 PM New York


def test_newer_intraday_close_beats_lagging_daily_source(tmp_path: Path) -> None:
    async def run() -> None:
        fred, yahoo = FakeAt("fred", "daily", 7175.83, THU), FakeAt("yahoo", "poll", 6910.39, FRI_CLOSE)
        mgr = make(tmp_path, [ChainEntry("fred", "SP500"), ChainEntry("yahoo", "^GSPC")], {"fred": fred, "yahoo": yahoo},
                   schedule=mh.US_EQUITY)
        await settle(mgr, 4)
        q = mgr.view(1)
        assert q["provider"] == "yahoo" and q["price"] == 6910.39
        assert q["fallback"] is False and "19 Nov 2026" in q["newer_note"] and "20 Nov 2026" in q["newer_note"]

        fred.ts, fred.price = FRI, 6910.39  # FRED publishes Friday: the official source is shown again
        mgr.next_poll.clear()
        await settle(mgr, 4)
        q = mgr.view(1)
        assert q["provider"] == "fred" and q["newer_note"] is None and q["fallback"] is False

    asyncio.run(run())


def test_daily_source_kept_when_backup_is_not_newer_or_fails(tmp_path: Path) -> None:
    async def run() -> None:
        tsy, yahoo = FakeAt("treasury", "daily", 4.92, FRI), FakeAt("yahoo", "poll", 4.912, FRI_CLOSE)
        mgr = make(tmp_path, [ChainEntry("treasury", "10 Yr"), ChainEntry("yahoo", "^TNX")], {"treasury": tsy, "yahoo": yahoo},
                   schedule=mh.US_EQUITY)
        await settle(mgr, 4)
        assert mgr.view(1)["provider"] == "treasury"  # same trading day: official wins

        yahoo.fail = True
        mgr.next_poll.clear()
        await settle(mgr, 4)
        q = mgr.view(1)
        assert q["provider"] == "treasury" and q["price"] == 4.92 and q["error_message"] is None and q["status"] != "ERROR"

    asyncio.run(run())


def test_us_stock_ignores_after_hours_trades(tmp_path: Path) -> None:
    async def run() -> None:
        a = FakePoll("a", 100.0)
        mgr = make(tmp_path, [ChainEntry("a", "X")], {"a": a}, schedule=mh.US_EQUITY)
        asset = mgr.assets[1]
        mgr._session_cache[mh.US_EQUITY] = (time.monotonic(), mh.SessionInfo(False, "Outside trading hours", None, None))
        close = QuoteData(price=100.0, source_timestamp=utcnow(), prev_close=99.0)
        mgr._apply(asset, asset.quote_chain[0], a, close, "stats")
        after_hours = QuoteData(price=97.0, source_timestamp=utcnow())
        mgr._apply(asset, asset.quote_chain[0], a, after_hours, "trade")
        q = mgr.view(1)
        assert q["price"] == 100.0 and q["change"] == 1.0  # the regular-session close stays

    asyncio.run(run())

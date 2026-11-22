"""Chart source choice: a daily official series (FRED) gives way to Yahoo when Yahoo has a newer US trading day."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.db.migrations import init_db
from app.db.session import Database
from app.providers.base import CandleData, Provider, ProviderError
from app.services.assets import ChainEntry, load_assets, seed_assets
from app.services.candles import CandleService
from app.services.provider_health import ProviderHealth
from app.services.status import daily_stamp
from app.timeutil import utcnow

# Relative to today, so the tests keep working: "Friday" = yesterday, "Thursday" = the day before.
FRI = utcnow().date() - timedelta(days=1)
THU = FRI - timedelta(days=1)


class FakeFred(Provider):
    name, label, kind, close_only = "fred", "Fake FRED", "daily", True

    def __init__(self, last: date) -> None:
        super().__init__(http=None)  # type: ignore[arg-type]
        self.last = last

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        days = [self.last - timedelta(days=k) for k in range(60)][::-1]
        return [CandleData(ts=daily_stamp(d), open=100.0, high=100.0, low=100.0, close=100.0) for d in days]


class FakeYahoo(Provider):
    name, label, unofficial = "yahoo", "Fake Yahoo", True

    def __init__(self, last: date) -> None:
        super().__init__(http=None)  # type: ignore[arg-type]
        self.last, self.fail = last, False

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        if self.fail:
            raise ProviderError("Fake Yahoo: simulated outage")
        out = []
        for k in range(60 if timeframe == "1d" else 3)[::-1]:
            day = self.last - timedelta(days=k)
            open_ = datetime.combine(day, time(14, 30), tzinfo=timezone.utc)  # 9:30 AM New York
            if timeframe == "1d":
                out.append(CandleData(ts=open_, open=99.0, high=101.0, low=98.0, close=99.5))
                continue
            step = timedelta(minutes=5 if timeframe == "5m" else 60)
            t = open_
            while t < open_ + timedelta(hours=6, minutes=30):
                out.append(CandleData(ts=t, open=99.0, high=101.0, low=98.0, close=99.5, volume=10.0))
                t += step
        return out


def make(tmp_path: Path, fred: FakeFred, yahoo: FakeYahoo):  # noqa: ANN201
    db = Database(tmp_path / "c.db")
    init_db(db)
    seed_assets(db)
    spx = next(a for a in load_assets(db) if a.symbol == "SPX")
    spx.candle_chain = [ChainEntry("fred", "SP500", "S&P 500 daily close"), ChainEntry("yahoo", "^GSPC", "S&P 500 index")]
    providers = {"fred": fred, "yahoo": yahoo}
    health = {n: ProviderHealth(name=n, label=p.label, unofficial=p.unofficial) for n, p in providers.items()}
    return CandleService(db, providers, health), spx


def test_chart_uses_yahoo_when_it_has_a_newer_trading_day(tmp_path: Path) -> None:
    async def run() -> None:
        svc, spx = make(tmp_path, FakeFred(THU), FakeYahoo(FRI))
        day = await svc.chart(spx, "1D")
        assert day["provider"] == "yahoo" and day["timeframe"] == "5m" and day["close_only"] is False
        assert f"{THU.day} {THU:%b %Y}" in day["newer_note"] and f"{FRI.day} {FRI:%b %Y}" in day["newer_note"]
        last = datetime.fromtimestamp(day["candles"][-1]["time"], timezone.utc).date()
        assert last == FRI and not day["problems"]
        year = await svc.chart(spx, "6M")
        assert year["provider"] == "yahoo" and year["timeframe"] == "1d"
        s = await svc.series(spx, "5m", utcnow() - timedelta(days=4))
        assert s["provider"] == "yahoo" and s["newer_note"]

    asyncio.run(run())


def test_chart_keeps_official_source_on_the_same_day_or_when_yahoo_fails(tmp_path: Path) -> None:
    async def run() -> None:
        yahoo = FakeYahoo(FRI)
        svc, spx = make(tmp_path, FakeFred(FRI), yahoo)
        same = await svc.chart(spx, "1M")
        assert same["provider"] == "fred" and same["newer_note"] is None and same["close_only"] is True

        svc2, spx2 = make(tmp_path / "b", FakeFred(THU), FakeYahoo(FRI))
        svc2.providers["yahoo"].fail = True  # type: ignore[attr-defined]
        down = await svc2.chart(spx2, "1D")
        assert down["provider"] == "fred" and down["newer_note"] is None and down["problems"] == []

    asyncio.run(run())

"""Full price history: weekly/monthly bars, performance table, pre-1970 dates, history ranges in the API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.providers import yahoo
from app.services import candles as cs


def day(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, tzinfo=timezone.utc)  # Yahoo stamps US daily bars at the 09:30 ET open


def test_weekly_and_monthly_bars() -> None:
    # Mon 2 - Fri 6 Nov 2026 and Mon 9 Nov 2026
    daily = [(day(2026, 11, d), 10.0 + d, 20.0 + d, 5.0 + d, 11.0 + d, 100.0) for d in (2, 3, 4, 5, 6, 9)]
    w = cs.aggregate(daily, "1wk")
    assert len(w) == 2
    t, o, h, lo, c, v = w[0]
    assert t == day(2026, 11, 2) and o == 12.0 and h == 26.0 and lo == 7.0 and c == 17.0 and v == 500.0
    m = cs.aggregate(daily + [(day(2026, 12, 1), 1, 2, 0.5, 1.5, None)], "1mo")
    assert len(m) == 2 and m[0][4] == 20.0 and m[1][5] is None


def test_performance_table() -> None:
    # A price that doubles every year for 6 years (one bar per ~month).
    start = day(2020, 1, 2)
    rows = []
    for i in range(0, 6 * 365 + 1, 30):
        t = start + timedelta(days=i)
        rows.append(SimpleNamespace(ts=t, close=100.0 * 2 ** (i / 365.25), high=0, low=0))
    p = cs.performance_table(rows)
    periods = {r["label"]: r for r in p["periods"]}
    assert "10 years" not in periods  # longer than the history: left out, never invented
    # monthly sample points, so the 1-year start is up to a month earlier than exactly one year
    assert 100 <= periods["1 year"]["change_pct"] < 112 and periods["1 year"]["cagr_pct"] is None
    assert abs(periods["3 years"]["cagr_pct"] - 100) < 4
    assert abs(periods["5 years"]["cagr_pct"] - 100) < 3
    assert periods["All history"]["start_date"] == "2020-01-02"
    assert p["highest_close"]["date"] == p["last_date"] and p["from_high_pct"] == 0


def test_pre_1970_daily_bars_parse() -> None:
    # The S&P 500 starts on 30 Dec 1927 (negative Unix time; Windows' fromtimestamp rejects it).
    d = {"chart": {"result": [{"timestamp": [-1325635200], "indicators": {"quote": [
        {"open": [17.66], "high": [17.66], "low": [17.66], "close": [17.66], "volume": [0]}]}}]}}
    c = yahoo.parse_candles(d)
    assert c[0].ts == datetime(1927, 12, 30, tzinfo=timezone.utc) and c[0].close == 17.66


def test_history_ranges_in_api_and_page() -> None:
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        st = c.app.state.mlw
        nvda = next(a for a in st.feeds.assets.values() if a.symbol == "NVDA")
        btc = next(a for a in st.feeds.assets.values() if a.symbol == "BTC")
        assert st.candles.history_ranges(nvda) == ["YTD", "5Y", "MAX"]
        assert st.candles.history_ranges(btc) == []  # no full-history source for crypto
        assert c.get("/api/candles/NVDA?range=10Y").status_code == 400
        page = c.get("/asset/NVDA").text
        assert 'data-range="MAX"' in page and 'data-range="YTD"' in page and 'id="perf-card"' in page
        btc_page = c.get("/asset/BTC").text
        assert 'data-range="MAX"' not in btc_page and 'id="perf-card"' not in btc_page

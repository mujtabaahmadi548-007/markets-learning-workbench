"""Status badge logic: LIVE / DELAYED / MARKET_CLOSED / STALE / ERROR (+ CONNECTING, UNAVAILABLE)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import status as st

NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def s(**kw) -> st.StatusResult:
    base = dict(now=NOW, has_price=True, kind="stream", market_open=True, source_timestamp=NOW,
                last_success_at=NOW, poll_interval=60.0)
    base.update(kw)
    return st.compute_status(st.StatusInput(**base))


def test_live_only_for_fresh_stream() -> None:
    assert s(source_timestamp=NOW - timedelta(seconds=3)).status == st.LIVE
    assert s(source_timestamp=NOW - timedelta(seconds=8)).status == st.DELAYED  # stream, but not fresh


def test_polled_data_is_never_live() -> None:
    r = s(kind="poll", source_timestamp=NOW - timedelta(seconds=1))
    assert r.status == st.DELAYED


def test_delayed_source_within_its_window() -> None:
    # Yahoo-style ~10 min delay with a 30 min expected delay -> still DELAYED, not STALE
    assert s(kind="poll", source_timestamp=NOW - timedelta(minutes=10), expected_delay=1800).status == st.DELAYED


def test_stale_when_open_market_data_too_old() -> None:
    assert s(kind="poll", source_timestamp=NOW - timedelta(minutes=20), expected_delay=0, poll_interval=15).status == st.STALE
    assert s(kind="stream", source_timestamp=NOW - timedelta(minutes=5)).status == st.STALE


def test_market_closed_beats_staleness() -> None:
    r = s(market_open=False, source_timestamp=NOW - timedelta(hours=40))
    assert r.status == st.MARKET_CLOSED


def test_error_when_fetch_failing_and_data_old() -> None:
    r = s(kind="poll", source_timestamp=NOW - timedelta(hours=1), last_success_at=NOW - timedelta(hours=1),
          last_error_at=NOW - timedelta(seconds=5), error_message="Yahoo: HTTP 500")
    assert r.status == st.ERROR and "HTTP 500" in r.detail and "last success" in r.detail


def test_recent_error_but_fresh_data_is_not_error() -> None:
    r = s(kind="poll", source_timestamp=NOW - timedelta(seconds=20), last_success_at=NOW - timedelta(seconds=20),
          last_error_at=NOW - timedelta(seconds=5), error_message="blip")
    assert r.status == st.DELAYED


def test_no_data_yet() -> None:
    assert s(has_price=False, source_timestamp=None, last_success_at=None).status == st.CONNECTING
    r = s(has_price=False, source_timestamp=None, last_success_at=None, last_error_at=NOW, error_message="boom")
    assert r.status == st.ERROR


def test_unavailable() -> None:
    r = s(unavailable_reason="No free official source publishes the ICE US Dollar Index (DXY).")
    assert r.status == st.UNAVAILABLE and r.label == "Not available on free sources"


def test_daily_series() -> None:
    assert s(kind="daily", source_timestamp=NOW - timedelta(days=1), market_open=False).status == st.DELAYED
    assert s(kind="daily", source_timestamp=NOW - timedelta(days=9)).status == st.STALE
    # A weekly-published series may be 9 days old and still current.
    assert s(kind="daily", source_timestamp=NOW - timedelta(days=9), daily_max_age_days=10).status == st.DELAYED


def test_fmt_age() -> None:
    assert st.fmt_age(5) == "5 s"
    assert st.fmt_age(600) == "10 min"
    assert st.fmt_age(3 * 3600 + 120) == "3 h 2 min"

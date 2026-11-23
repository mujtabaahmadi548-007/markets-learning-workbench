"""Market-hours logic: weekends, holidays, early closes, futures breaks, daylight saving."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import market_hours as mh
from app.timeutil import NEW_YORK


def ny(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=NEW_YORK).astimezone(timezone.utc)


def test_crypto_always_open() -> None:
    assert mh.is_open(mh.CRYPTO, ny(2026, 11, 22, 3))  # Sunday night
    assert mh.last_close(mh.CRYPTO, ny(2026, 11, 22, 3)) is None


def test_us_equity_regular_hours() -> None:
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 11, 19, 9, 29))
    assert mh.is_open(mh.US_EQUITY, ny(2026, 11, 19, 9, 30))
    assert mh.is_open(mh.US_EQUITY, ny(2026, 11, 19, 15, 59))
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 11, 19, 16, 0))


def test_us_equity_weekend() -> None:
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 11, 21, 12))  # Saturday
    info = mh.session_info(mh.US_EQUITY, ny(2026, 11, 21, 12))
    assert info.reason == "Weekend"
    assert info.last_close == ny(2026, 11, 20, 16)  # Friday 16:00
    assert info.next_open == ny(2026, 11, 23, 9, 30)  # Monday 09:30


def test_thanksgiving_and_early_close() -> None:
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 11, 26, 11))  # Thanksgiving
    assert mh.session_info(mh.US_EQUITY, ny(2026, 11, 26, 11)).reason == "Holiday: Thanksgiving Day"
    assert mh.is_open(mh.US_EQUITY, ny(2026, 11, 27, 12, 59))  # day after: early close 13:00
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 11, 27, 13, 0))
    assert mh.last_close(mh.US_EQUITY, ny(2026, 11, 27, 15)) == ny(2026, 11, 27, 13)


def test_christmas_eve_early_close_and_christmas() -> None:
    assert mh.is_open(mh.US_EQUITY, ny(2026, 12, 24, 12))
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 12, 24, 13, 30))
    assert not mh.is_open(mh.US_EQUITY, ny(2026, 12, 25, 11))


def test_futures_week_and_daily_break() -> None:
    assert not mh.is_open(mh.FUTURES, ny(2026, 11, 22, 17, 59))  # Sunday before 18:00
    assert mh.is_open(mh.FUTURES, ny(2026, 11, 22, 18, 0))  # Sunday open
    assert mh.is_open(mh.FUTURES, ny(2026, 11, 23, 16, 59))
    assert not mh.is_open(mh.FUTURES, ny(2026, 11, 23, 17, 30))  # daily break
    assert mh.session_info(mh.FUTURES, ny(2026, 11, 23, 17, 30)).reason == "Daily break"
    assert not mh.is_open(mh.FUTURES, ny(2026, 11, 20, 17, 0))  # Friday close
    assert not mh.is_open(mh.FUTURES, ny(2026, 11, 21, 10))  # Saturday


def test_spot_metals_open_on_thanksgiving_but_not_christmas() -> None:
    assert mh.is_open(mh.SPOT, ny(2026, 11, 26, 11))
    assert not mh.is_open(mh.FUTURES, ny(2026, 11, 26, 11))  # approximation: futures closed on NYSE holidays
    assert not mh.is_open(mh.SPOT, ny(2026, 12, 25, 11))


def test_daylight_saving_shift() -> None:
    # The open is 09:30 New York time both sides of the 8 Mar 2026 change: 14:30 UTC (winter) -> 13:30 UTC (summer).
    assert mh.next_open(mh.US_EQUITY, ny(2026, 3, 6, 18)) == ny(2026, 3, 9, 9, 30)
    assert ny(2026, 3, 9, 9, 30) == datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)
    assert ny(2026, 3, 5, 9, 30) == datetime(2026, 3, 5, 14, 30, tzinfo=timezone.utc)


def test_us_equity_phases_and_session_start() -> None:
    assert mh.us_equity_phase(ny(2026, 11, 23, 3, 59)) == "closed"
    assert mh.us_equity_phase(ny(2026, 11, 23, 4, 0)) == "pre"
    assert mh.us_equity_phase(ny(2026, 11, 23, 12, 53)) == "regular"
    assert mh.us_equity_phase(ny(2026, 11, 23, 16, 30)) == "post"
    assert mh.us_equity_phase(ny(2026, 11, 23, 20, 0)) == "closed"
    assert mh.us_equity_phase(ny(2026, 11, 22, 12, 0)) == "closed"  # Sunday
    assert mh.us_equity_phase(ny(2026, 11, 26, 12, 0)) == "closed"  # Thanksgiving
    assert mh.us_equity_phase(ny(2026, 11, 27, 14, 0)) == "post"  # early close 13:00 the day after
    assert mh.session_start(mh.US_EQUITY, ny(2026, 11, 23, 12, 53)) == ny(2026, 11, 23, 9, 30)
    assert mh.session_start(mh.FUTURES, ny(2026, 11, 23, 12, 53)) == ny(2026, 11, 22, 18, 0)
    assert mh.session_start(mh.US_EQUITY, ny(2026, 11, 22, 12, 0)) is None
    info = mh.session_info(mh.US_EQUITY, ny(2026, 11, 23, 7, 0))
    assert not info.open and info.phase == "pre" and "Pre-market" in info.reason


def test_holiday_calendar_for_the_browser() -> None:
    cal = mh.holiday_calendar([2026])
    assert "2026-11-26" in cal["nyse_holidays"] and "2026-12-25" in cal["nyse_holidays"]
    assert "2026-11-27" in cal["nyse_early_close"] and "2026-12-24" in cal["nyse_early_close"]

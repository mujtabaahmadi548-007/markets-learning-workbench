"""Market-hours service: is a market open right now, when did it last close, when does it open next.

Schedules (all times US Eastern, which handles daylight saving automatically):
- crypto_24_7   : always open.
- us_equity     : NYSE regular session 09:30-16:00, Mon-Fri. Closed on NYSE holidays.
                  Early close 13:00 on the day after Thanksgiving, on 24 Dec and on 3 Jul (when trading days).
- futures_24_5  : CME/ICE futures (oil, gas, dollar index): Sun 18:00 -> Fri 17:00, with a daily
                  17:00-18:00 break. Treated as closed on NYSE holidays (approximation: CME often
                  trades a shortened session on US holidays).
- spot_24_5     : spot gold/silver (over-the-counter): same hours as futures_24_5 but only closed
                  for Christmas Day and New Year's Day.
Pre-market and after-hours trading are not counted as "open" for stocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import holidays

from app.timeutil import NEW_YORK, UTC, ensure_utc

CRYPTO = "crypto_24_7"
US_EQUITY = "us_equity"
FUTURES = "futures_24_5"
SPOT = "spot_24_5"
SCHEDULES = (CRYPTO, US_EQUITY, FUTURES, SPOT)

STEP = timedelta(minutes=30)  # every session boundary falls on a :00 or :30


@lru_cache(maxsize=16)
def _nyse_holidays(year: int) -> dict[date, str]:
    return dict(holidays.financial_holidays("NYSE", years=[year]))


def nyse_holiday(d: date) -> str | None:
    return _nyse_holidays(d.year).get(d)


def is_nyse_trading_day(d: date) -> bool:
    return d.weekday() < 5 and nyse_holiday(d) is None


def nyse_close_time(d: date) -> time:
    """16:00, or 13:00 on early-close days."""
    early = False
    if d.month == 11 and d.weekday() == 4:  # day after Thanksgiving (4th Thursday of November)
        early = nyse_holiday(d - timedelta(days=1)) == "Thanksgiving Day"
    if (d.month, d.day) in ((12, 24), (7, 3)):
        early = True
    return time(13, 0) if early else time(16, 0)


def _ny(now: datetime) -> datetime:
    return ensure_utc(now).astimezone(NEW_YORK)


def _open_24_5(local: datetime, holiday_check) -> bool:  # noqa: ANN001
    wd, t = local.weekday(), local.time()
    if holiday_check(local.date()):
        return False
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday: opens 18:00
        return t >= time(18, 0)
    if wd == 4:  # Friday: closes 17:00
        return t < time(17, 0)
    # Mon-Thu: daily break 17:00-18:00
    return not (time(17, 0) <= t < time(18, 0))


def is_open(schedule: str, now: datetime) -> bool:
    if schedule == CRYPTO:
        return True
    local = _ny(now)
    if schedule == US_EQUITY:
        d = local.date()
        if not is_nyse_trading_day(d):
            return False
        return time(9, 30) <= local.time() < nyse_close_time(d)
    if schedule == FUTURES:
        return _open_24_5(local, lambda d: nyse_holiday(d) is not None)
    if schedule == SPOT:
        return _open_24_5(local, lambda d: (d.month, d.day) in ((12, 25), (1, 1)))
    raise ValueError(f"unknown schedule {schedule!r}")


def _floor_step(dt: datetime) -> datetime:
    dt = ensure_utc(dt).replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 30)


def last_close(schedule: str, now: datetime) -> datetime | None:
    """Most recent moment the market went from open to closed (UTC). None for 24/7 markets."""
    if schedule == CRYPTO:
        return None
    t = _floor_step(now)
    for _ in range(16 * 48):
        if t <= now and not is_open(schedule, t) and is_open(schedule, t - timedelta(minutes=1)):
            return t
        t -= STEP
    return None


def next_open(schedule: str, now: datetime) -> datetime | None:
    """Next moment the market opens (UTC). None for 24/7 markets or if it is open now."""
    if schedule == CRYPTO or is_open(schedule, now):
        return None
    t = _floor_step(now) + STEP
    for _ in range(16 * 48):
        if is_open(schedule, t) and not is_open(schedule, t - timedelta(minutes=1)):
            return t
        t += STEP
    return None


@dataclass
class SessionInfo:
    open: bool
    reason: str  # short text for the UI
    last_close: datetime | None
    next_open: datetime | None


def session_info(schedule: str, now: datetime) -> SessionInfo:
    now = ensure_utc(now)
    if schedule == CRYPTO:
        return SessionInfo(True, "Open 24/7", None, None)
    open_now = is_open(schedule, now)
    if open_now:
        return SessionInfo(True, "Market open", last_close(schedule, now), None)
    local = _ny(now)
    hol = nyse_holiday(local.date()) if schedule in (US_EQUITY, FUTURES) else None
    if hol:
        reason = f"Holiday: {hol}"
    elif local.weekday() >= 5 and not (schedule != US_EQUITY and local.weekday() == 6 and local.time() >= time(18, 0)):
        reason = "Weekend"
    elif schedule != US_EQUITY and time(17, 0) <= local.time() < time(18, 0):
        reason = "Daily break"
    else:
        reason = "Outside trading hours"
    return SessionInfo(False, reason, last_close(schedule, now), next_open(schedule, now))


def now_utc() -> datetime:
    return datetime.now(UTC)

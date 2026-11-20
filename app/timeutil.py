"""Time helpers. Rule: store UTC (timezone-aware), display Asia/Jakarta."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
JAKARTA = ZoneInfo("Asia/Jakarta")
NEW_YORK = ZoneInfo("America/New_York")
PACIFIC = ZoneInfo("America/Los_Angeles")  # Gemini's daily quota resets at midnight Pacific time


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Return an aware UTC datetime. Naive input is rejected to avoid silent timezone bugs."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime given; all datetimes must be timezone-aware")
    return dt.astimezone(UTC)


def to_jakarta(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(JAKARTA)


def fmt_jakarta(dt: datetime | None) -> str:
    """e.g. '20 Nov 2026, 03:15 WIB'."""
    if dt is None:
        return "—"
    local = to_jakarta(dt)
    return local.strftime("%d %b %Y, %H:%M WIB").lstrip("0")


def fmt_utc(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return ensure_utc(dt).strftime("%Y-%m-%d %H:%M:%S UTC")


def gemini_day_start(now: datetime | None = None) -> datetime:
    """Start (in UTC) of the current Gemini quota day, which begins at midnight Pacific time."""
    now = ensure_utc(now or utcnow())
    local = now.astimezone(PACIFIC)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def gemini_next_reset(now: datetime | None = None) -> datetime:
    """Next Gemini quota reset (midnight Pacific), returned in UTC."""
    now = ensure_utc(now or utcnow())
    local = now.astimezone(PACIFIC)
    next_midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.astimezone(UTC)

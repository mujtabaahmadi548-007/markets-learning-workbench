"""Status badge logic: LIVE / DELAYED / MARKET_CLOSED / STALE / ERROR (+ CONNECTING, UNAVAILABLE).

Rules (spec section 5):
- LIVE          only for a streaming feed whose last update is at most 5 seconds old.
- DELAYED       polled or delayed data that is still within its expected refresh window.
                The label says how old the data is (the browser keeps the age ticking).
- MARKET_CLOSED the market is closed per its schedule; the last price and the close time are shown.
- STALE         the market should be open but the data is older than expected.
- ERROR         fetching failed and the data we have is too old (or there is none).
- CONNECTING    no data yet since the app started (not an error, not a price).
- UNAVAILABLE   no free source exists for this asset ("Not available on free sources").
Delayed data is never labelled LIVE, and stale data is never shown as current.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

LIVE = "LIVE"
DELAYED = "DELAYED"
MARKET_CLOSED = "MARKET_CLOSED"
STALE = "STALE"
ERROR = "ERROR"
CONNECTING = "CONNECTING"
UNAVAILABLE = "UNAVAILABLE"

LIVE_MAX_AGE = 5.0  # seconds
DAILY_STALE_DAYS = 5  # a daily series older than this (calendar days) is stale


@dataclass(frozen=True)
class StatusInput:
    now: datetime
    has_price: bool
    kind: str  # stream | poll | daily
    market_open: bool
    source_timestamp: datetime | None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    error_message: str | None = None
    poll_interval: float = 60.0
    expected_delay: float = 0.0  # provider's documented/typical delay in seconds
    unavailable_reason: str | None = None
    daily_max_age_days: int = DAILY_STALE_DAYS  # weekly-published daily series need a longer limit


@dataclass(frozen=True)
class StatusResult:
    status: str
    label: str
    detail: str = ""


def fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{s // 86400} d"


def stale_after(kind: str, poll_interval: float, expected_delay: float) -> float:
    """Seconds after which open-market data counts as STALE."""
    if kind == "stream":
        return 120.0
    return expected_delay + max(3 * poll_interval, 180.0)


def compute_status(i: StatusInput) -> StatusResult:
    if i.unavailable_reason:
        return StatusResult(UNAVAILABLE, "Not available on free sources", i.unavailable_reason)
    recent_error = i.last_error_at is not None and (i.last_success_at is None or i.last_error_at > i.last_success_at)

    if not i.has_price or i.source_timestamp is None:
        if recent_error:
            return StatusResult(ERROR, "Error", f"Fetch failed: {i.error_message or 'unknown error'}")
        return StatusResult(CONNECTING, "Connecting…", "Waiting for the first price from the source")

    age = (i.now - i.source_timestamp).total_seconds()

    if i.kind == "daily":
        days = (i.now.date() - i.source_timestamp.date()).days
        if days > i.daily_max_age_days:
            return StatusResult(STALE, "Stale", f"Daily data; the newest value is {days} days old")
        detail = "Official daily data (one value per business day)"
        if recent_error:
            detail += f". Last refresh failed: {i.error_message}"
        return StatusResult(DELAYED, "Daily", detail)

    if not i.market_open:
        return StatusResult(MARKET_CLOSED, "Market closed", "Showing the last price before the close")

    limit = stale_after(i.kind, i.poll_interval, i.expected_delay)
    if recent_error and age > limit:
        since = f"; last success {fmt_age((i.now - i.last_success_at).total_seconds())} ago" if i.last_success_at else ""
        return StatusResult(ERROR, "Error", f"Fetch failed: {i.error_message or 'unknown error'}{since}")
    if i.kind == "stream" and age <= LIVE_MAX_AGE:
        return StatusResult(LIVE, "Live", "Streaming; updated within the last 5 seconds")
    if age > limit:
        return StatusResult(STALE, "Stale", f"Market is open but the last update is {fmt_age(age)} old")
    if i.kind == "stream":
        return StatusResult(DELAYED, "Delayed", "Stream connected; no new trade in the last few seconds")
    return StatusResult(DELAYED, "Delayed", "Checked on a timer, not streamed")


def daily_stamp(d) -> datetime:  # noqa: ANN001 - date
    """Timestamp used for a daily value (only the date is known): 00:00 UTC of that date."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

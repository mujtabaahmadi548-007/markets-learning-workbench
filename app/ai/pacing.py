"""Spread the website's daily Gemini budget over Google's whole quota day.

Google's free quota day starts at midnight Pacific time (3:00 PM Jakarta in winter, 2:00 PM in summer). The
website builds run about every 10 minutes, day and night. Without pacing they used the whole daily limit within
about 10 hours of the reset, so the 7:00 AM Jakarta briefing and all morning AI work (breakdowns, "why", quiz
questions) waited until the next reset in the afternoon.

Now `limit - reserve` calls are spread evenly over the quota day, and `reserve` calls stay free for the daily
briefing until it has been made.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.timeutil import JAKARTA, ensure_utc, gemini_day_start, gemini_next_reset, utcnow

BRIEFING_HOUR = 7  # Jakarta time
HEAD_START = 1 / 24  # one hour's share may be used ahead, so work right after a reset is not held back


def briefing_date_in_quota_day(now: datetime | None = None) -> str:
    """Jakarta date (YYYY-MM-DD) of the 7:00 AM Jakarta briefing that falls inside the current quota day."""
    local_start = gemini_day_start(now).astimezone(JAKARTA)
    t = local_start.replace(hour=BRIEFING_HOUR, minute=0, second=0, microsecond=0)
    if t < local_start:
        t += timedelta(days=1)
    return t.date().isoformat()


def paced_allowance(limit: int, reserve: int, briefing_done: bool, now: datetime | None = None) -> int:
    """How many Gemini calls may have been used by `now` in the current quota day."""
    now = ensure_utc(now or utcnow())
    start, end = gemini_day_start(now), gemini_next_reset(now)
    share = min(1.0, max(0.0, (now - start) / (end - start)) + HEAD_START)
    reserve = max(0, min(reserve, limit))
    return int((limit - reserve) * share) + (reserve if briefing_done else 0)

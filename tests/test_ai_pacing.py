"""The website's Gemini budget lasts the whole quota day and keeps room for the 7:00 AM Jakarta briefing."""
from datetime import UTC, datetime

from app.ai.pacing import briefing_date_in_quota_day, paced_allowance


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def test_briefing_date_is_the_7am_jakarta_inside_the_quota_day() -> None:
    # Winter (PST): the quota day runs 08:00 UTC -> 08:00 UTC; 7:00 AM Jakarta is 00:00 UTC, the next calendar day.
    assert briefing_date_in_quota_day(utc(2026, 11, 21, 9, 0)) == "2026-11-22"
    assert briefing_date_in_quota_day(utc(2026, 11, 21, 23, 59)) == "2026-11-22"
    assert briefing_date_in_quota_day(utc(2026, 11, 22, 0, 30)) == "2026-11-22"  # 7:30 AM Jakarta
    assert briefing_date_in_quota_day(utc(2026, 11, 22, 7, 59)) == "2026-11-22"
    assert briefing_date_in_quota_day(utc(2026, 11, 22, 8, 1)) == "2026-11-23"  # after the reset
    # Summer (PDT): the quota day starts at 07:00 UTC.
    assert briefing_date_in_quota_day(utc(2026, 7, 10, 7, 30)) == "2026-07-11"


def test_budget_is_spread_over_the_day() -> None:
    start = utc(2026, 11, 21, 8, 0)  # midnight Pacific
    assert paced_allowance(150, 20, False, start) == int(130 / 24)  # one hour's share right after the reset
    assert paced_allowance(150, 20, False, utc(2026, 11, 21, 20, 0)) == int(130 * (13 / 24))  # 12 h in
    # Late in the day only the reserve is left - it is kept for the briefing until that is made.
    assert paced_allowance(150, 20, False, utc(2026, 11, 22, 7, 59)) == 130
    assert paced_allowance(150, 20, True, utc(2026, 11, 22, 7, 59)) == 150


def test_reserve_is_available_at_briefing_time() -> None:
    # 7:00 AM Jakarta = 00:00 UTC, 16 h into the quota day: other work may only have used its paced share,
    # so the briefing's reserve is still free.
    at_7am = utc(2026, 11, 22, 0, 0)
    assert paced_allowance(150, 20, False, at_7am) <= 150 - 20
    assert paced_allowance(150, 20, False, at_7am) == int(130 * (17 / 24))


def test_reserve_never_exceeds_the_limit() -> None:
    assert paced_allowance(10, 50, False, utc(2026, 11, 22, 7, 59)) == 0
    assert paced_allowance(10, 50, True, utc(2026, 11, 22, 7, 59)) == 10

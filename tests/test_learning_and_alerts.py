"""Spaced repetition, alerts, calendar parsing, lesson formatting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import srs
from app.services.alerts import should_trigger
from app.services.calendar import parse_bls_ics, parse_fomc, parse_forexfactory
from app.services.learning import render_markup
from app.services.learning_content import LESSONS, SEED_CONCEPTS
from app.web import autolink

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)


def test_sm2_correct_answers_grow_interval() -> None:
    st, due = srs.review(srs.SrsState(), True, NOW)
    assert st.repetitions == 1 and st.interval_days == 1.0 and due == NOW + timedelta(days=1)
    st, due = srs.review(st, True, NOW)
    assert st.interval_days == 6.0
    st, due = srs.review(st, True, NOW)
    assert st.interval_days == round(6.0 * st.ease, 1) and st.interval_days > 6


def test_sm2_missed_question_comes_back_soon() -> None:
    st, _ = srs.review(srs.SrsState(), True, NOW)
    st, due = srs.review(st, False, NOW)
    assert st.repetitions == 0 and due == NOW + timedelta(minutes=10) and st.ease < 2.5
    for _ in range(10):
        st, _ = srs.review(st, False, NOW)
    assert st.ease == srs.MIN_EASE


def test_alert_conditions() -> None:
    assert should_trigger("above", 100, 100.0, None)
    assert not should_trigger("above", 100, 99.99, None)
    assert should_trigger("below", 50, 49.0, None)
    assert should_trigger("pct_move", 2, 10.0, -2.5)
    assert not should_trigger("pct_move", 2, 10.0, None)
    assert not should_trigger("above", 1, None, None)


def test_bls_ics_parsing() -> None:
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc-1
DTSTART;TZID=US-Eastern:20261210T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
UID:abc-2
DTSTART;TZID=US-Eastern:20261218T100000
SUMMARY:Work Experience of the Population (Annual)
END:VEVENT
END:VCALENDAR"""
    ev = parse_bls_ics(ics)
    assert len(ev) == 1 and ev[0]["title"] == "Consumer Price Index"
    assert ev[0]["event_time"] == datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)  # 08:30 EST


def test_fomc_parsing() -> None:
    html = ('<h4><a>2026 FOMC Meetings</a></h4><div class="row fomc-meeting"><div class="fomc-meeting__month col">'
            '<strong>December</strong></div><div class="fomc-meeting__date col">8-9*</div></div>')
    ev = parse_fomc(html)
    assert ev[0]["event_time"] == datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc)  # 14:00 EST


def test_forexfactory_keeps_high_impact_usd_only() -> None:
    rows = [{"title": "CPI m/m", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "High"},
            {"title": "GDP", "country": "JPY", "date": "2026-12-10T08:30:00-05:00", "impact": "High"},
            {"title": "Retail", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "Low"}]
    ev = parse_forexfactory(rows)
    assert len(ev) == 1 and ev[0]["is_unofficial"]


def test_lessons_and_concepts_are_consistent() -> None:
    slugs = {s for s, _, _ in SEED_CONCEPTS}
    for _, _, title, _, concept_slugs, content in LESSONS:
        assert set(concept_slugs) <= slugs, title
        assert content.strip()


def test_markup_and_autolink() -> None:
    html = render_markup("Para one with **bold**.\n\n- a\n- b\n\n<script>x</script>")
    assert "<strong>bold</strong>" in html and "<ul><li>a</li><li>b</li></ul>" in html and "<script>" not in html
    linked = str(autolink("The bid-ask spread and the spread widened <b>", [
        {"slug": "spread", "name": "Price spread"}, {"slug": "bid-ask-spread", "name": "Bid-ask spread"}]))
    assert linked.count("<a ") == 1 and 'href="/glossary/bid-ask-spread"' in linked and "&lt;b&gt;" in linked

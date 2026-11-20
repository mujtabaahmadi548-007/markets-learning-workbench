"""Economic calendar from verified free sources (stored in calendar_events).

- BLS release calendar (official .ics): CPI, jobs report (Employment Situation), PPI, JOLTS, Employment Cost Index.
- Federal Reserve FOMC meeting calendar (official page): statement at 14:00 New York time on the last meeting day.
- ForexFactory weekly calendar (UNOFFICIAL, only when ALLOW_UNOFFICIAL_SOURCES=true): high-impact USD events.
No free source for OPEC meeting dates was found, so OPEC is not listed (OPEC news still arrives via the news feeds).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.session import Database
from app.models.tables import CalendarEvent
from app.timeutil import NEW_YORK, utcnow

log = logging.getLogger(__name__)

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FF_WEEKS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
BLS_KEEP = {"Consumer Price Index": "high", "Employment Situation": "high", "Producer Price Index": "medium",
            "Job Openings and Labor Turnover Survey": "medium", "Employment Cost Index": "medium", "Real Earnings": "low"}
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], start=1)}


def parse_bls_ics(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"SUMMARY:(.+)", block)
        start = re.search(r"DTSTART;TZID=US-Eastern:(\d{8}T\d{6})", block)
        uid = re.search(r"UID:(.+)", block)
        if not (summary and start):
            continue
        name = summary.group(1).strip()
        if name not in BLS_KEEP:
            continue
        local = datetime.strptime(start.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=NEW_YORK)
        out.append({"external_id": f"bls:{(uid.group(1).strip() if uid else name + start.group(1))}", "source": "BLS",
                    "title": name, "country": "US", "event_time": local.astimezone(timezone.utc),
                    "importance": BLS_KEEP[name], "url": "https://www.bls.gov/schedule/news_release/", "is_unofficial": False})
    return out


def parse_fomc(html_text: str) -> list[dict[str, Any]]:
    out = []
    for ym in re.finditer(r"(\d{4}) FOMC Meetings", html_text):
        year = int(ym.group(1))
        nxt = re.search(r"\d{4} FOMC Meetings", html_text[ym.end():])
        section = html_text[ym.end(): ym.end() + nxt.start()] if nxt else html_text[ym.end():]
        for m in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>\s*([\d\-]+)\*?', section, re.S):
            month_txt, days = m.group(1), m.group(2)
            month_name = month_txt.split("/")[-1]
            if month_name not in MONTHS:
                continue
            last_day = int(days.split("-")[-1])
            month = MONTHS[month_name]
            if "/" in month_txt and "-" in days and int(days.split("-")[-1]) < int(days.split("-")[0]):
                pass  # meeting spanning two months: the last day is in the second month (already chosen)
            try:
                local = datetime(year, month, last_day, 14, 0, tzinfo=NEW_YORK)
            except ValueError:
                continue
            out.append({"external_id": f"fomc:{year}-{month:02d}-{last_day:02d}", "source": "Federal Reserve",
                        "title": "FOMC rate decision (statement)", "country": "US", "event_time": local.astimezone(timezone.utc),
                        "importance": "high", "url": FOMC_PAGE, "is_unofficial": False})
    return out


def parse_forexfactory(rows: list[dict]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("country") != "USD" or r.get("impact") != "High":
            continue
        try:
            t = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"external_id": f"ff:{r['title']}:{t.isoformat()}", "source": "ForexFactory (unofficial)",
                    "title": r["title"], "country": "US", "event_time": t, "importance": "high",
                    "url": "https://www.forexfactory.com/calendar", "is_unofficial": True})
    return out


class CalendarService:
    def __init__(self, db: Database, http: httpx.AsyncClient, allow_unofficial: bool) -> None:
        self.db = db
        self.http = http
        self.allow_unofficial = allow_unofficial
        self.status: dict[str, str] = {}

    async def refresh(self) -> int:
        events: list[dict[str, Any]] = []
        try:
            r = await self.http.get(BLS_ICS, timeout=20)
            r.raise_for_status()
            events += parse_bls_ics(r.text)
            self.status["BLS"] = "ok"
        except Exception as e:  # noqa: BLE001
            self.status["BLS"] = f"error: {type(e).__name__}"
        try:
            r = await self.http.get(FOMC_PAGE, timeout=20)
            r.raise_for_status()
            events += parse_fomc(r.text)
            self.status["FOMC"] = "ok"
        except Exception as e:  # noqa: BLE001
            self.status["FOMC"] = f"error: {type(e).__name__}"
        if self.allow_unofficial:
            results = []
            for url in FF_WEEKS:  # "next week" is only published late in the week, so a 404 there is normal
                try:
                    r = await self.http.get(url, timeout=20)
                    if r.status_code == 200:
                        events += parse_forexfactory(r.json())
                    results.append(r.status_code == 200)
                except Exception:  # noqa: BLE001
                    results.append(False)
            self.status["ForexFactory"] = "ok" if any(results) else "error"
        if events:
            import asyncio

            await asyncio.to_thread(self._save, events)
        return len(events)

    def _save(self, events: list[dict[str, Any]]) -> None:
        with self.db.session() as s:
            for e in events:
                stmt = sqlite_insert(CalendarEvent).values(**e, fetched_at=utcnow())
                stmt = stmt.on_conflict_do_update(index_elements=["external_id"],
                                                  set_={"title": e["title"], "event_time": e["event_time"], "fetched_at": utcnow()})
                s.execute(stmt)

    def upcoming(self, hours: int = 72, past_hours: int = 6) -> list[dict[str, Any]]:
        now = utcnow()
        with self.db.session() as s:
            rows = s.scalars(select(CalendarEvent).where(CalendarEvent.event_time >= now - timedelta(hours=past_hours),
                                                          CalendarEvent.event_time <= now + timedelta(hours=hours))
                             .order_by(CalendarEvent.event_time)).all()
        # ForexFactory duplicates official CPI/jobs entries: keep the official one when both exist on the same hour.
        seen_hours = {(r.event_time.replace(minute=0, second=0, microsecond=0)) for r in rows if not r.is_unofficial}
        out = []
        for r in rows:
            if r.is_unofficial and r.event_time.replace(minute=0, second=0, microsecond=0) in seen_hours:
                continue
            out.append({"title": r.title, "source": r.source, "time": r.event_time.isoformat(), "importance": r.importance,
                        "url": r.url, "unofficial": r.is_unofficial, "past": r.event_time < now})
        return out

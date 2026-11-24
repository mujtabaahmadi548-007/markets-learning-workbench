"""Daily briefing (07:00 Jakarta, or on demand).

Contents:
1. Overnight moves - a table of REAL stored quotes (price, change, what the change is measured against,
   status, source, time), copied at generation time.
2. Top 5 stories (importance x recency) with links to their breakdown pages.
3. "What to watch today" - 3 sentences from Gemini based ONLY on those stories and the calendar
   (numbers validated). Skipped with a note if AI is unavailable.
4. Upcoming calendar events (next 48 h) and 3 review quiz questions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from sqlalchemy import select

from app.ai.gemini_client import AIDailyLimitReached, AIError, AIQuotaExceeded
from app.ai.schemas import BriefingWatch
from app.ai.validator import generate_validated
from app.models.tables import Briefing
from app.timeutil import JAKARTA, utcnow

log = logging.getLogger(__name__)


class BriefingService:
    def __init__(self, db, gemini, feeds, news, calendar, learning, breakdowns) -> None:  # noqa: ANN001
        self.db = db
        self.gemini = gemini
        self.feeds = feeds
        self.news = news
        self.calendar = calendar
        self.learning = learning
        self.breakdowns = breakdowns

    def get(self, day: date | None = None) -> dict[str, Any] | None:
        with self.db.session() as s:
            stmt = select(Briefing).order_by(Briefing.date.desc())
            if day:
                stmt = select(Briefing).where(Briefing.date == day)
            b = s.scalars(stmt).first()
            return None if b is None else {"date": b.date.isoformat(), "content": b.json_content, "created_at": b.created_at.isoformat()}

    def dates(self) -> list[str]:
        with self.db.session() as s:
            return [d.isoformat() for d in s.scalars(select(Briefing.date).order_by(Briefing.date.desc()).limit(60))]

    async def generate(self, auto: bool = False) -> dict[str, Any]:
        today = utcnow().astimezone(JAKARTA).date()
        moves = []
        for v in self.feeds.views():
            moves.append({k: v.get(k) for k in ("symbol", "name", "asset_class", "decimals", "currency", "price", "change",
                                                 "change_pct", "change_basis", "quote_currency", "status", "source",
                                                 "source_timestamp", "kind", "is_proxy", "is_unofficial", "fallback",
                                                 "newer_note")})
        stories = await asyncio.to_thread(self.news.query, hours=24, sort="important", limit=5, distinct_stories=True)
        # The 07:00 run also writes breakdowns for the top 3 stories (within the AI budget). On-demand runs skip
        # this to stay quick; the story links open the article page, where a breakdown is one click away.
        for st in (stories[:3] if auto else []):
            if not st["has_breakdown"] and self.gemini.configured and self.gemini.can_run_background():
                try:
                    await self.breakdowns.create_async(st["id"])
                    st["has_breakdown"] = True
                except AIError as e:
                    log.warning("Briefing: breakdown for %d skipped: %s", st["id"], e)
                    break
        events = await asyncio.to_thread(self.calendar.upcoming, 48, 0)
        content = {
            "generated_at": utcnow().isoformat(), "moves": moves, "stories": stories, "events": events,
            "review": await asyncio.to_thread(self.learning.review_sample, 3),
        }
        content.update(await self._watch(stories, events))
        await asyncio.to_thread(self._save, today, content)
        return self.get(today)  # type: ignore[return-value]

    async def _watch(self, stories: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
        """'What to watch today' from the briefing's own stories and calendar (never newer ones)."""
        watch, watch_note, validated, failed, error = [], "", True, False, ""
        if not stories and not events:
            watch_note = "Not enough information: no stored stories or calendar events yet."
        elif not self.gemini.configured:
            watch_note = "AI is not configured, so 'what to watch' is skipped."
        else:
            lines = [f"[{s['id']}] {s['source_name']}: {s['title']}" + (f" — {(s.get('description') or '')[:250]}" if s.get("description") else "")
                     for s in stories]
            ev = [f"- {e['time']} UTC: {e['title']} ({e['source']})" for e in events]
            prompt = "\n".join([
                "Write exactly 3 short sentences for a learner: what to watch in markets today.",
                "Base them ONLY on these stored stories and calendar events. No predictions presented as fact, no advice.",
                "", "TOP STORIES:", *(lines or ["(none)"]), "", "CALENDAR (next 48 hours, times in UTC):", *(ev or ["(none)"]),
            ])
            try:
                out, validated, watch_note, _ = await asyncio.to_thread(generate_validated, self.gemini, "briefing", prompt, BriefingWatch)
                watch = out.what_to_watch[:3]  # type: ignore[attr-defined]
            except AIError as e:
                # Google's raw error text is long and technical: keep it in the log, show a short note.
                log.warning("Briefing: 'what to watch' failed: %s", e)
                failed, error = True, f"{type(e).__name__}: {str(e)[:200]}"  # AIError texts are already redacted
                watch_note = ("The AI summary could not be written yet because the free daily AI limit was reached. "
                              "It is tried again automatically later today." if isinstance(e, AIQuotaExceeded | AIDailyLimitReached)
                              else "The AI summary could not be written yet (the AI service did not answer). "
                                   "It is tried again automatically later today.")
        return {"what_to_watch": watch, "watch_note": watch_note, "watch_validated": validated, "watch_failed": failed,
                "watch_error": error, "watch_at": utcnow().isoformat()}

    def needs_watch_retry(self) -> bool:
        """True when the newest briefing's 'what to watch' failed because of the AI (quota, outage)."""
        b = self.get()
        if b is None or b["content"].get("what_to_watch"):
            return False
        c = b["content"]
        # briefings saved before 'watch_failed' existed stored Google's raw error as "AI unavailable: ..."
        return bool(c.get("watch_failed") or str(c.get("watch_note", "")).startswith("AI unavailable"))

    async def retry_watch(self) -> str:
        """Write the missing 'what to watch' for the newest briefing. Prices, stories and calendar stay exactly as
        they were at briefing time; only the AI summary (based on those same stories and events) is added."""
        b = self.get()
        if b is None:
            return "no briefing"
        c = dict(b["content"])
        if c.get("what_to_watch"):
            return "already written"
        c.update(await self._watch(c.get("stories") or [], c.get("events") or []))
        await asyncio.to_thread(self._save, date.fromisoformat(b["date"]), c, False)
        if c["what_to_watch"]:
            return "written"
        return f"still unavailable ({c['watch_error'] or c['watch_note']})"

    def _save(self, day: date, content: dict[str, Any], touch: bool = True) -> None:
        with self.db.session() as s:
            b = s.scalar(select(Briefing).where(Briefing.date == day))
            if b is None:
                s.add(Briefing(date=day, json_content=content, created_at=utcnow()))
            else:
                b.json_content = content
                if touch:  # a later summary retry keeps the briefing's own time
                    b.created_at = utcnow()

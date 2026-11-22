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

from app.ai.gemini_client import AIError
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
        watch, watch_note, validated = [], "", True
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
                watch_note = f"AI unavailable: {e}"
        content = {
            "generated_at": utcnow().isoformat(), "moves": moves, "stories": stories, "events": events,
            "what_to_watch": watch, "watch_note": watch_note, "watch_validated": validated,
            "review": await asyncio.to_thread(self.learning.review_sample, 3),
        }
        await asyncio.to_thread(self._save, today, content)
        return self.get(today)  # type: ignore[return-value]

    def _save(self, day: date, content: dict[str, Any]) -> None:
        with self.db.session() as s:
            b = s.scalar(select(Briefing).where(Briefing.date == day))
            if b is None:
                s.add(Briefing(date=day, json_content=content, created_at=utcnow()))
            else:
                b.json_content, b.created_at = content, utcnow()

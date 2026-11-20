"""'Why did it move today?' (spec 9B).

Input given to Gemini (real data only): the asset's price, its change and what that change is measured
against, the previous close / 24 h-ago price, the source and time; the US dollar index and 10-year yield
moves when available; and today's tagged news (ids, titles, descriptions).
Every driver must cite at least one of those article ids. If the news does not explain the move, the
answer says "No clear news driver found." With no tagged news at all, no AI call is made.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.ai.gemini_client import AIError, GeminiService
from app.ai.schemas import MoveExplanationOut
from app.ai.validator import generate_validated
from app.db.session import Database
from app.models.tables import MoveExplanation
from app.timeutil import JAKARTA, utcnow

log = logging.getLogger(__name__)

NO_DRIVER = "No clear news driver found."
BASIS_TEXT = {"24h": "over the last 24 hours", "prev_close": "versus the previous close",
              "prev_obs": "versus the previous day's value", "24h_recorded": "versus the price recorded 24 hours ago"}


def _fmt(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


class MoveExplainer:
    def __init__(self, db: Database, gemini: GeminiService, news, feeds) -> None:  # noqa: ANN001
        self.db = db
        self.gemini = gemini
        self.news = news
        self.feeds = feeds

    def latest(self, asset_id: int) -> dict[str, Any] | None:
        today = utcnow().astimezone(JAKARTA).date()
        with self.db.session() as s:
            m = s.scalars(select(MoveExplanation).where(MoveExplanation.asset_id == asset_id, MoveExplanation.date == today)
                          .order_by(MoveExplanation.created_at.desc())).first()
            return None if m is None else self._out(m)

    def _out(self, m: MoveExplanation) -> dict[str, Any]:
        return {"id": m.id, "date": m.date.isoformat(), "price_change_pct": m.price_change_pct, "content": m.json_content,
                "article_ids": m.article_ids_used, "model": m.model_used, "validation_passed": m.validation_passed,
                "created_at": m.created_at.isoformat()}

    def _data_block(self, q: dict[str, Any]) -> list[str]:
        d = q["decimals"]
        lines = [f"ASSET: {q['name']} ({q['symbol']})",
                 f"PRICE NOW: {_fmt(q['price'], d)} {q.get('quote_currency') or ''} (source: {q.get('source')}, time UTC: {q.get('source_timestamp')}, status: {q['status']})"]
        basis = BASIS_TEXT.get(q.get("change_basis") or "", "versus the previous close")
        if q["currency"] == "%":
            lines.append(f"CHANGE {basis.upper()}: {_fmt(q['change'], 2)} percentage points (previous value {_fmt(q['prev_close'], d)})")
        else:
            lines.append(f"CHANGE {basis.upper()}: {_fmt(q['change'], d)} ({_fmt(q['change_pct'], 2)}%), reference price {_fmt(q['prev_close'], d)}")
        if q.get("day_high") is not None:
            lines.append(f"RANGE: low {_fmt(q['day_low'], d)}, high {_fmt(q['day_high'], d)}")
        return lines

    async def explain(self, symbol: str, force: bool = False) -> dict[str, Any]:
        asset = next((a for a in self.feeds.assets.values() if a.symbol == symbol.upper()), None)
        if asset is None:
            raise AIError("Unknown asset")
        q = self.feeds.view(asset.id)
        if q["price"] is None or q["change"] is None:
            return {"error": "This asset has no price change yet (its source gives no previous close; the app shows a "
                             "24-hour change after recording prices for 24 hours). Nothing to explain."}
        if not force:
            existing = await asyncio.to_thread(self.latest, asset.id)
            if existing and utcnow() - datetime.fromisoformat(existing["created_at"]) < timedelta(hours=1):
                return existing
        hours = 30 if asset.schedule != "crypto_24_7" else 24
        articles = await asyncio.to_thread(self.news.query, symbol=asset.symbol, hours=hours, sort="important", limit=12,
                                           min_confidence=0.5)
        today = utcnow().astimezone(JAKARTA).date()
        if not articles:
            content = {"summary": NO_DRIVER, "likely_drivers": [], "unexplained": True,
                       "note": f"No news tagged to {asset.symbol} in the last {hours} hours, so there is nothing to cite.",
                       "data": self._data_block(q), "articles": []}
            return await asyncio.to_thread(self._save, asset.id, today, q, content, [], "none (no AI call)", True)

        macro = []
        for sym in ("DXY", "US10Y"):
            if sym == asset.symbol:
                continue
            m = next((a for a in self.feeds.assets.values() if a.symbol == sym), None)
            if m:
                mv = self.feeds.view(m.id)
                if mv["price"] is not None and mv["change"] is not None and mv["status"] not in ("ERROR", "STALE", "UNAVAILABLE"):
                    macro += [f"  {x}" for x in self._data_block(mv)]
        art_lines = []
        for a in articles:
            desc = (a.get("description") or "")[:300]
            art_lines.append(f"[{a['id']}] {a['source_name']} | published UTC {a.get('published_at')} | {a['title']}" + (f" — {desc}" if desc else ""))
        prompt = "\n".join([
            "Explain why this asset's price moved, using ONLY the data and the news items below.",
            "", "PRICE DATA:", *self._data_block(q),
            "", "RELATED MARKET MOVES:" if macro else "", *macro,
            "", "TODAY'S NEWS TAGGED TO THIS ASSET (cite these ids):", *art_lines, "",
            "Rules:",
            "- likely_drivers: each driver must cite at least one article id from the list in evidence_article_ids.",
            "- Only name a driver if an article actually reports something that plausibly moved this asset in this direction.",
            f"- If the articles do not explain the move, set unexplained=true and start the summary with \"{NO_DRIVER}\"",
            "- confidence: low / medium / high, judged only from how directly the articles explain the move.",
            "- note: one sentence of caution (news timing does not prove cause and effect).",
            "- summary: 2-3 simple sentences for a learner. Use only numbers from the data above.",
        ])
        out, passed, notes, model = await asyncio.to_thread(generate_validated, self.gemini, "why_move", prompt, MoveExplanationOut)
        data = out.model_dump()
        valid_ids = {a["id"] for a in articles}
        drivers = []
        for dr in data["likely_drivers"]:
            ids = [i for i in dr["evidence_article_ids"] if i in valid_ids]
            if ids:  # a driver without real evidence is dropped
                dr["evidence_article_ids"] = ids
                drivers.append(dr)
        data["likely_drivers"] = drivers
        if not drivers:
            data["unexplained"] = True
        if data["unexplained"] and not data["summary"].startswith(NO_DRIVER):
            data["summary"] = f"{NO_DRIVER} {data['summary']}".strip()
        used = sorted({i for d in drivers for i in d["evidence_article_ids"]})
        data["data"] = self._data_block(q)
        data["articles"] = [{"id": a["id"], "title": a["title"], "source_name": a["source_name"], "url": a["url"],
                             "published_at": a["published_at"]} for a in articles if a["id"] in used]
        data["validation_notes"] = notes
        return await asyncio.to_thread(self._save, asset.id, today, q, data, used, model, passed)

    def _save(self, asset_id: int, day, q, content, used, model, passed) -> dict[str, Any]:  # noqa: ANN001
        with self.db.session() as s:
            m = MoveExplanation(asset_id=asset_id, date=day, price_change_pct=q.get("change_pct"), json_content=content,
                                article_ids_used=used, model_used=model, validation_passed=passed, created_at=utcnow())
            s.add(m)
            s.flush()
            return self._out(m)


__all__ = ["MoveExplainer", "NO_DRIVER"]

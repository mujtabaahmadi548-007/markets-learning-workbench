"""AI article breakdowns (spec 9A) and the real "price since publish" table.

A breakdown is generated from the text the app actually fetched. If only the headline/description
is available, basis = "headline_and_description_only" and the page says "Based on headline and
description only." Every number is checked by the number validator.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.ai.gemini_client import AIError, GeminiService
from app.ai.schemas import ArticleBreakdown
from app.ai.validator import generate_validated
from app.db.session import Database
from app.models.tables import ArticleAsset, Asset, Breakdown, Concept, NewsArticle
from app.providers import rss
from app.services.textutil import canonical_url
from app.timeutil import fmt_jakarta, utcnow

log = logging.getLogger(__name__)

BASIS_FULL = "full_article"
BASIS_HEADLINE = "headline_and_description_only"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:90] or "concept"


def breakdown_prompt(a: NewsArticle, symbols: dict[str, str], basis: str) -> str:
    sym_list = ", ".join(f"{s} ({n})" for s, n in symbols.items())
    pub = a.published_at.isoformat() if a.published_at else "not given"
    if basis == BASIS_FULL:
        text = f"FULL ARTICLE TEXT:\n{(a.body_text or '')[:9000]}"
    else:
        text = (f"DESCRIPTION:\n{a.description or '(none)'}\n\n"
                "NOTE: only the headline and the description are available, not the full article.")
    return f"""Break down this news article for a learner.

SOURCE: {a.source_name}
PUBLISHED (UTC): {pub}
HEADLINE: {a.title}
{text}

TRACKED ASSETS (the only symbols allowed in affected_assets): {sym_list}

Write JSON with these fields:
- what_happened: 2-3 plain-English sentences, only from the article.
- why_it_matters: why this matters for markets in general (from the article's content; no outside facts).
- affected_assets: tracked assets the article gives a reason to connect. direction_suggested_by_article is what the
  ARTICLE suggests (up / down / unclear) - not your prediction. mechanism: the cause -> effect chain in words.
- concepts: 2-5 market concepts a learner needs to understand this (slug = lowercase-with-hyphens, name, one_line definition).
- facts_vs_opinion: "facts" = things the article reports as having happened; "opinions_or_speculation" = forecasts,
  views and guesses, written as "The article says ..." / "An analyst quoted expects ...".
- beginner_explanation: 3-5 sentences, explain like I am learning.
- basis: "{basis}".
Use numbers only if they appear in the article text above. If something is not in the text, write "Not enough information."
"""


class BreakdownService:
    def __init__(self, db: Database, gemini: GeminiService, news, http, symbols: dict[str, str]) -> None:  # noqa: ANN001
        self.db = db
        self.gemini = gemini
        self.news = news
        self.http = http
        self.symbols = symbols
        self.failed: dict[int, float] = {}  # article id -> time of last failure (skip in auto mode for a while)

    # ------------------------------------------------------------------ read
    def get(self, article_id: int) -> dict[str, Any] | None:
        with self.db.session() as s:
            b = s.scalars(select(Breakdown).where(Breakdown.article_id == article_id).order_by(Breakdown.created_at.desc())).first()
            if b is None:
                return None
            return {"id": b.id, "content": b.json_content, "model": b.model_used, "validation_passed": b.validation_passed,
                    "validation_notes": b.validation_notes, "created_at": b.created_at.isoformat()}

    # ------------------------------------------------------------------ create
    async def decode_link(self, article_id: int) -> None:
        """Find the publisher URL behind a Google News link (quick; used when you open an article)."""
        a = await asyncio.to_thread(self._load, article_id)
        if a is not None and a.original_url is None and "news.google.com" in a.url:
            decoded = await rss.decode_google_news_url(self.http, a.url)
            if decoded:
                await asyncio.to_thread(self.news._set_original, article_id, canonical_url(decoded))

    async def ensure_text(self, article_id: int) -> None:
        """Decode a Google News link and fetch the article text if that has not been tried yet."""
        a = await asyncio.to_thread(self._load, article_id)
        if a is None:
            return
        if a.original_url is None and "news.google.com" in a.url:
            decoded = await rss.decode_google_news_url(self.http, a.url)
            if decoded:
                await asyncio.to_thread(self.news._set_original, article_id, canonical_url(decoded))
                a = await asyncio.to_thread(self._load, article_id)
        if a and a.original_url and not a.body_attempted:
            await self.news.fetch_body(article_id, a.original_url)

    def _load(self, article_id: int) -> NewsArticle | None:
        with self.db.session() as s:
            return s.get(NewsArticle, article_id)

    def create(self, article_id: int) -> dict[str, Any]:
        """Generate (or regenerate) the breakdown. Blocking: run in a worker thread."""
        with self.db.session() as s:
            a = s.get(NewsArticle, article_id)
            if a is None:
                raise AIError("Article not found")
            basis = BASIS_FULL if a.body_fetched and a.body_text and len(a.body_text) >= 500 else BASIS_HEADLINE
            prompt = breakdown_prompt(a, self.symbols, basis)
        try:
            out, passed, notes, model = generate_validated(self.gemini, "breakdown", prompt, ArticleBreakdown, tier="smart")
        except AIError:
            self.failed[article_id] = utcnow().timestamp()
            raise
        data = out.model_dump()
        data["basis"] = basis  # the truth about what text was used, whatever the model wrote
        data["affected_assets"] = [x for x in data["affected_assets"] if x["symbol"].upper() in self.symbols]
        for x in data["affected_assets"]:
            x["symbol"] = x["symbol"].upper()
        for c in data["concepts"]:
            c["slug"] = slugify(c.get("slug") or c.get("name", ""))
        with self.db.session() as s:
            s.add(Breakdown(article_id=article_id, model_used=model, json_content=data, validation_passed=passed,
                            validation_notes=notes or None, created_at=utcnow()))
            # Concepts mentioned become glossary entries (explained in full the first time they are opened).
            for c in data["concepts"]:
                if not s.scalar(select(Concept.id).where(Concept.slug == c["slug"])):
                    s.add(Concept(slug=c["slug"], name=c["name"][:120], short_definition=c["one_line"], category=None,
                                  related_concepts=[], origin="ai", created_at=utcnow()))
            # AI-identified affected assets also become tags (if the keyword rules missed them).
            have = set(s.scalars(select(Asset.symbol).join(ArticleAsset, ArticleAsset.asset_id == Asset.id)
                                 .where(ArticleAsset.article_id == article_id)))
            for x in data["affected_assets"]:
                if x["symbol"] not in have:
                    asset_id = s.scalar(select(Asset.id).where(Asset.symbol == x["symbol"]))
                    if asset_id:
                        s.add(ArticleAsset(article_id=article_id, asset_id=asset_id, tag_method="ai", confidence=0.7))
        log.info("Breakdown for article %d created (%s, validation %s)", article_id, model, "passed" if passed else "FAILED")
        return self.get(article_id)  # type: ignore[return-value]

    async def create_async(self, article_id: int) -> dict[str, Any]:
        await self.ensure_text(article_id)
        return await asyncio.to_thread(self.create, article_id)

    async def auto_run(self, max_count: int = 1) -> int:
        """Background: breakdowns for the most important recent tagged articles (within the AI budget)."""
        done = 0
        for _ in range(max_count):
            if not self.gemini.can_run_background():
                break
            cand = await asyncio.to_thread(self._next_candidate)
            if cand is None:
                break
            try:
                await self.create_async(cand)
                done += 1
            except AIError as e:
                log.warning("Auto breakdown for article %d skipped: %s", cand, e)
                break
        return done

    def _next_candidate(self) -> int | None:
        now = utcnow()
        done_stories = {x["story"] for x in self.news.query(hours=36, has_breakdown=True, limit=200)}
        items = [x for x in self.news.query(hours=24, sort="important", has_breakdown=False, limit=40, distinct_stories=True)
                 if x["story"] not in done_stories]
        for it in items:
            if not it["tags"] or max(t["confidence"] for t in it["tags"]) < 0.7:
                continue
            last_fail = self.failed.get(it["id"])
            if last_fail and now.timestamp() - last_fail < 6 * 3600:
                continue
            return it["id"]
        return None


# ---------------------------------------------------------------- price since publish (real data only)
async def moves_since_publish(article: NewsArticle, symbols: list[str], feeds, candles) -> list[dict[str, Any]]:  # noqa: ANN001
    """For each asset: price just before the article was published vs now, from ONE source (the chart source),
    so the two prices are always the same instrument. Returns rows with the source and the exact times used."""
    out = []
    if article.published_at is None:
        return [{"symbol": s, "error": "The feed gave no publish time, so the move since publication cannot be measured."}
                for s in symbols]
    pub = article.published_at
    age = utcnow() - pub
    tf = "5m" if age <= timedelta(hours=40) else ("1h" if age <= timedelta(days=30) else "1d")
    step = {"5m": timedelta(minutes=5), "1h": timedelta(hours=1), "1d": timedelta(days=1)}[tf]
    for sym in symbols:
        asset = next((a for a in feeds.assets.values() if a.symbol == sym), None)
        if asset is None:
            continue
        series = await candles.series(asset, tf, pub - timedelta(days=5))
        rows = series.get("candles") or []
        if not rows:
            out.append({"symbol": sym, "name": asset.name, "error": "; ".join(series.get("problems") or ["No price history available"])})
            continue
        step_eff = timedelta(days=1) if series.get("close_only") else step
        before = [c for c in rows if c["ts"] + step_eff <= pub]
        if not before:
            out.append({"symbol": sym, "name": asset.name, "error": "Price history does not reach back to the publish time."})
            continue
        then = before[-1]
        now_price, now_ts, now_src = rows[-1]["close"], rows[-1]["ts"] + step_eff, series["source"]
        q = feeds.view(asset.id)
        if q.get("provider") == series.get("provider") and q.get("provider_symbol") == series.get("symbol") and q.get("price"):
            from datetime import datetime

            now_price, now_ts = q["price"], datetime.fromisoformat(q["source_timestamp"])
        if then["ts"] + step_eff > now_ts or then["close"] == 0:
            continue
        pct = (now_price - then["close"]) / then["close"] * 100
        out.append({
            "symbol": sym, "name": asset.name, "decimals": asset.decimals, "currency": asset.currency,
            "then_price": then["close"], "then_time": (then["ts"] + step_eff).isoformat(),
            "now_price": now_price, "now_time": now_ts.isoformat(), "change_pct": pct,
            "change_abs": now_price - then["close"], "source": now_src, "note": series.get("note"),
            "is_proxy": series.get("is_proxy"), "is_unofficial": series.get("is_unofficial"),
            "daily": bool(series.get("close_only")), "timeframe": series.get("timeframe"),
        })
    return out


def describe_basis(basis: str) -> str:
    return "Based on the full article text." if basis == BASIS_FULL else "Based on headline and description only."


__all__ = ["BreakdownService", "moves_since_publish", "describe_basis", "slugify", "fmt_jakarta"]

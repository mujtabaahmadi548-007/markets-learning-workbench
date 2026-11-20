"""News, article breakdowns, price-since-publish and 'Why did it move?' endpoints + pages."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.deps import state
from app.api.pages import _ctx
from app.models.tables import ArticleAsset, Asset
from app.services.assets import CLASS_LABELS
from app.services.breakdowns import describe_basis, moves_since_publish
from app.web import templates

router = APIRouter()


@router.get("/api/news")
def news_list(request: Request, symbol: str | None = None, asset_class: str | None = None, source: str | None = None,
              hours: float = 24, has_breakdown: str | None = None, q: str | None = None, sort: str = "latest",
              limit: int = 100, distinct: bool = False) -> dict[str, Any]:
    st = state(request)
    hb = {"yes": True, "no": False}.get((has_breakdown or "").lower())
    arts = st.news.query(symbol=symbol or None, asset_class=asset_class or None, source=source or None,
                         hours=hours if hours > 0 else None, has_breakdown=hb, q=q or None, sort=sort, limit=min(limit, 300),
                         distinct_stories=distinct)
    return {"articles": arts, "last_run": st.news.last_run.isoformat() if st.news.last_run else None}


@router.get("/api/news/sources")
def news_sources(request: Request) -> dict[str, Any]:
    return {"sources": state(request).news.sources()}


@router.post("/api/news/refresh")
async def news_refresh(request: Request) -> dict[str, Any]:
    stats = await state(request).news.run(include_google=True)
    return {"new": stats.new, "fetched": stats.fetched, "duplicates": stats.duplicates, "errors": stats.errors[:10]}


def _article_dict(request: Request, article_id: int) -> dict[str, Any]:
    st = state(request)
    items = st.news.query(ids=[article_id], hours=None, limit=1)
    if not items:
        raise HTTPException(404, "Article not found")
    a = st.news.article(article_id)
    d = items[0]
    d.update({"body_fetched": a.body_fetched, "body_error": a.body_error, "body_attempted": a.body_attempted,
              "original_url": a.original_url, "google_url": a.url if "news.google.com" in a.url else None,
              "body_chars": len(a.body_text or "")})
    return d


@router.get("/api/articles/{article_id}")
def article_api(request: Request, article_id: int) -> dict[str, Any]:
    st = state(request)
    return {"article": _article_dict(request, article_id), "breakdown": st.breakdowns.get(article_id)}


@router.post("/api/articles/{article_id}/breakdown")
async def article_breakdown(request: Request, article_id: int) -> dict[str, Any]:
    st = state(request)
    if not st.gemini.configured:
        raise HTTPException(503, "Gemini is not configured (GEMINI_API_KEY in .env).")
    b = await st.breakdowns.create_async(article_id)
    return {"breakdown": b}


@router.get("/api/articles/{article_id}/moves")
async def article_moves(request: Request, article_id: int) -> dict[str, Any]:
    st = state(request)
    a = st.news.article(article_id)
    if a is None:
        raise HTTPException(404, "Article not found")
    with st.db.session() as s:
        syms = list(s.scalars(select(Asset.symbol).join(ArticleAsset, ArticleAsset.asset_id == Asset.id)
                              .where(ArticleAsset.article_id == article_id, ArticleAsset.confidence >= 0.5)))
    b = st.breakdowns.get(article_id)
    if b:
        syms += [x["symbol"] for x in b["content"].get("affected_assets", [])]
    syms = [x for x in dict.fromkeys(syms) if any(asset.symbol == x for asset in st.feeds.assets.values())][:8]
    return {"moves": await moves_since_publish(a, syms, st.feeds, st.candles)}


@router.get("/api/assets/{symbol}/why")
def why_latest(request: Request, symbol: str) -> dict[str, Any]:
    st = state(request)
    asset = next((a for a in st.feeds.assets.values() if a.symbol == symbol.upper()), None)
    if asset is None:
        raise HTTPException(404, "Unknown asset")
    return {"explanation": st.explainer.latest(asset.id)}


@router.post("/api/assets/{symbol}/why")
async def why_generate(request: Request, symbol: str, force: bool = False) -> dict[str, Any]:
    st = state(request)
    if not st.gemini.configured:
        raise HTTPException(503, "Gemini is not configured (GEMINI_API_KEY in .env).")
    return {"explanation": await st.explainer.explain(symbol, force=force)}


@router.get("/api/assets/{symbol}/news")
def asset_news(request: Request, symbol: str, hours: float = 72, limit: int = 15) -> dict[str, Any]:
    return {"articles": state(request).news.query(symbol=symbol.upper(), hours=hours, sort="important", limit=limit)}


# ---------------------------------------------------------------- pages
@router.get("/news", response_class=HTMLResponse)
def news_page(request: Request) -> HTMLResponse:
    st = state(request)
    assets = sorted(st.feeds.assets.values(), key=lambda a: (a.sort_order, a.id))
    return templates.TemplateResponse(request, "news.html", _ctx(request, "/news", assets=assets, class_labels=CLASS_LABELS))


@router.get("/news/{article_id}", response_class=HTMLResponse)
async def article_page(request: Request, article_id: int) -> HTMLResponse:
    st = state(request)
    art_row = st.news.article(article_id)
    if st.http is not None and art_row is not None and os.environ.get("MLW_BUILD") != "1":
        try:
            await asyncio.wait_for(st.breakdowns.decode_link(article_id), timeout=12)
            art_row = st.news.article(article_id)
            if not art_row.image_url and art_row.image_source is None and art_row.original_url:
                await asyncio.wait_for(st.news.fetch_page_image(article_id, art_row.original_url), timeout=8)
        except Exception:  # noqa: BLE001 - the Google News link still works; an illustration is shown
            pass
    art = _article_dict(request, article_id)
    b = st.breakdowns.get(article_id)
    concepts = (b or {}).get("content", {}).get("concepts", []) if b else []
    if concepts:
        st.learning.mark_seen([c["slug"] for c in concepts])
    return templates.TemplateResponse(request, "article.html", _ctx(
        request, "/news", a=art, b=b, concepts=concepts,
        basis_text=(f"Based on the article text the app fetched ({art['body_chars']:,} characters)."
                    if b and b["content"]["basis"] == "full_article" else describe_basis(b["content"]["basis"]) if b else ""),
        ai_ready=st.gemini.configured))

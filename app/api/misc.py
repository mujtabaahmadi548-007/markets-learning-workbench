"""Briefing, calendar, alerts and settings pages + API."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.deps import state
from app.api.pages import _ctx
from app.services import settings_store as ss
from app.services.alerts import CONDITIONS
from app.services.assets import load_assets
from app.web import templates

router = APIRouter()


# ---------------------------------------------------------------- briefing
@router.get("/briefing", response_class=HTMLResponse)
def briefing_page(request: Request, day: str | None = None) -> HTMLResponse:
    st = state(request)
    b = st.briefing.get(date.fromisoformat(day)) if day else st.briefing.get()
    stories = []
    if b:  # current details (image, breakdown state) for the stories saved in the briefing, same order
        saved = [s["id"] for s in b["content"].get("stories", [])]
        current = {x["id"]: x for x in st.news.query(ids=saved, hours=None, limit=len(saved) or 1)}
        stories = [current[i] for i in saved if i in current]
    dates = st.briefing.dates()
    # Only the newest briefing shows current prices (with the briefing-time price kept under each one);
    # older ones stay a record of their own day.
    live_prices = bool(b and dates and b["date"] == dates[0])
    return templates.TemplateResponse(request, "briefing.html", _ctx(request, "/briefing", b=b, dates=dates,
                                                                      stories=stories, live_prices=live_prices,
                                                                      events=st.calendar.upcoming(24 * 14, 6),
                                                                      ai_ready=st.gemini.configured))


@router.post("/api/briefing/generate")
async def briefing_generate(request: Request) -> dict[str, Any]:
    return {"briefing": await state(request).briefing.generate(auto=False)}


@router.get("/api/calendar")
def calendar_api(request: Request, hours: int = 72) -> dict[str, Any]:
    st = state(request)
    return {"events": st.calendar.upcoming(hours, 6), "status": st.calendar.status}


# ---------------------------------------------------------------- alerts
@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request) -> HTMLResponse:
    st = state(request)
    assets = sorted(st.feeds.assets.values(), key=lambda a: (a.sort_order, a.id))
    return templates.TemplateResponse(request, "alerts.html", _ctx(request, "/alerts", alerts=st.alerts.list(), assets=assets,
                                                                    conditions=CONDITIONS))


class AlertIn(BaseModel):
    symbol: str
    condition: str
    value: float


@router.get("/api/alerts")
def alerts_list(request: Request) -> dict[str, Any]:
    return {"alerts": state(request).alerts.list()}


@router.post("/api/alerts")
def alerts_create(request: Request, body: AlertIn) -> dict[str, Any]:
    try:
        return {"id": state(request).alerts.create(body.symbol, body.condition, body.value)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/api/alerts/{alert_id}")
def alerts_delete(request: Request, alert_id: int) -> dict[str, Any]:
    state(request).alerts.delete(alert_id)
    return {"ok": True}


# ---------------------------------------------------------------- settings
_models_cache: dict[str, Any] = {}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    st = state(request)
    s = st.settings
    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, "/settings", s=s, keys=s.key_status(), watch=ss.watchlist(st.db), models=_models_cache.get("list", []),
        calls_today=st.gemini.calls_today()))


@router.get("/api/gemini/models")
async def gemini_models(request: Request) -> dict[str, Any]:
    st = state(request)
    if not st.gemini.configured:
        return {"models": []}
    if "list" not in _models_cache:
        models = await asyncio.to_thread(st.gemini.list_models)
        _models_cache["list"] = sorted(m["name"] for m in models if "generateContent" in m["supported_actions"]
                                       and m["name"].startswith("gemini"))
    return {"models": _models_cache["list"]}


class SettingsIn(BaseModel):
    gemini_model_fast: str | None = None
    gemini_model_smart: str | None = None
    gemini_daily_call_limit: int | None = None
    news_interval_min: int | None = None
    auto_breakdowns_per_hour: int | None = None


@router.post("/api/settings")
def settings_save(request: Request, body: SettingsIn) -> dict[str, Any]:
    st = state(request)
    try:
        saved = ss.save_overrides(st.db, st.settings, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if "news_interval_min" in saved and st.scheduler:
        st.scheduler.scheduler.reschedule_job("news_ingest", trigger="interval", minutes=st.settings.news_interval_min)
    if {"gemini_model_fast", "gemini_model_smart"} & set(saved):
        st.gemini._client_obj = None  # rebuild client lazily (models are read per call anyway)
    return {"saved": saved}


class EnvIn(BaseModel):
    name: str
    value: str


@router.post("/api/settings/env")
def settings_env(request: Request, body: EnvIn) -> dict[str, Any]:
    try:
        ss.set_env_line(body.name, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "restart_needed": True}


def _reload_assets(request: Request) -> None:
    st = state(request)
    assets = load_assets(st.db)
    st.feeds.set_assets(assets)
    enabled = {a.symbol: a.name for a in assets if a.enabled}
    st.news.symbols.clear()
    st.news.symbols.update(enabled)  # the same dict is shared with the breakdown service


@router.post("/api/watchlist/{symbol}/enabled")
def watch_enabled(request: Request, symbol: str, enabled: bool) -> dict[str, Any]:
    try:
        ss.set_enabled(state(request).db, symbol, enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _reload_assets(request)
    return {"ok": True}


@router.post("/api/watchlist/{symbol}/move")
def watch_move(request: Request, symbol: str, direction: int) -> dict[str, Any]:
    try:
        ss.move(state(request).db, symbol, direction)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _reload_assets(request)
    return {"ok": True}


class AddAsset(BaseModel):
    kind: str
    symbol: str
    name: str = ""


@router.post("/api/watchlist/add")
async def watch_add(request: Request, body: AddAsset) -> dict[str, Any]:
    st = state(request)
    sym = body.symbol.strip().upper()
    # Check that a real source knows this symbol before adding it.
    providers = st.feeds.providers
    try:
        if body.kind == "crypto":
            await providers["binance"].get_quote(f"{sym}USDT")
        elif body.kind == "stock":
            p = providers.get("finnhub") if providers["finnhub"].configured else providers.get("yahoo") or providers.get("twelvedata")
            if p is None or not p.configured:
                raise HTTPException(400, "No stock source is configured (add FINNHUB_API_KEY).")
            await p.get_quote(sym)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"No price found for {sym}: {e}") from None
    try:
        ss.add_asset(st.db, body.kind, sym, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _reload_assets(request)
    return {"ok": True, "symbol": sym}


@router.delete("/api/watchlist/{symbol}")
def watch_remove(request: Request, symbol: str) -> dict[str, Any]:
    try:
        ss.remove_custom(state(request).db, symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _reload_assets(request)
    return {"ok": True}

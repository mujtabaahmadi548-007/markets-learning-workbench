"""Core HTML pages: dashboard, asset page, health. (Other pages live next to their API routes.)"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.deps import state
from app.services.assets import CLASS_LABELS, CLASS_ORDER
from app.web import APP_VERSION, templates

router = APIRouter()

# Navigation, grouped: monitoring pages, learning pages, then system pages.
NAV = [
    {"href": "/", "label": "Dashboard", "stage": 0, "group": "Monitor"},
    {"href": "/news", "label": "News", "stage": 2},
    {"href": "/briefing", "label": "Briefing", "stage": 6},
    {"href": "/alerts", "label": "Alerts", "stage": 6},
    {"href": "/demo", "label": "Demo invest", "stage": 6, "sep": True},
    {"href": "/learn", "label": "Learn", "stage": 5, "group": "Learn"},
    {"href": "/glossary", "label": "Glossary", "stage": 5},
    {"href": "/quiz", "label": "Quiz", "stage": 5, "sep": True},
    {"href": "/settings", "label": "Settings", "stage": 6},
    {"href": "/health", "label": "Health", "stage": 0},
]
CURRENT_STAGE = 7


def _ctx(request: Request, page: str, **extra) -> dict:
    return {"nav": NAV, "current_stage": CURRENT_STAGE, "page": page, "version": APP_VERSION, **extra}


@router.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request) -> HTMLResponse:
    """Demo investing with pretend money at real prices (the account lives in the browser)."""
    return templates.TemplateResponse(request, "demo.html", _ctx(request, "/demo"))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    st = state(request)
    from app.timeutil import JAKARTA, utcnow

    today = utcnow().astimezone(JAKARTA).date()
    stats = st.learning.stats()
    next_lesson = None
    for p in st.learning.paths():
        nxt = next((l for l in p["lessons"] if not l["done"]), None)
        if nxt:
            next_lesson = {"id": nxt["id"], "title": nxt["title"], "path": p["title"]}
            break
    concepts = st.learning.concepts()
    pool = [c for c in concepts if not c["times_seen"]] or concepts
    spotlight = pool[today.toordinal() % len(pool)] if pool else None  # a different concept each day
    counts = st.news.counts(hours=24)
    latest = st.briefing.get()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(
        request, "/", keys=st.settings.key_status(), class_order=CLASS_ORDER, class_labels=CLASS_LABELS,
        model_fast=st.settings.model_fast, model_smart=st.settings.model_smart,
        events=st.calendar.upcoming(24 * 10, 2)[:5], stats=stats, next_lesson=next_lesson, spotlight=spotlight,
        news_count=counts["articles"], breakdown_count=counts["breakdowns"],
        alerts_active=sum(1 for a in st.alerts.list() if a["active"]),
        briefing_today=bool(latest and latest["date"] == today.isoformat()),
    ))


@router.get("/asset/{symbol}", response_class=HTMLResponse)
def asset_page(request: Request, symbol: str) -> HTMLResponse:
    st = state(request)
    asset = next((a for a in st.feeds.assets.values() if a.symbol.upper() == symbol.upper()), None)
    if asset is None:
        raise HTTPException(404, f"Unknown or disabled asset: {symbol}")
    concepts = []
    from app.services.learning_content import LESSONS

    slugs = []
    for _, _, _, syms, cs, _ in LESSONS:
        if asset.symbol in syms:
            slugs += cs
    for slug in dict.fromkeys(slugs):
        c = st.learning.concept(slug)
        if c:
            concepts.append(c)
    return templates.TemplateResponse(request, "asset.html", _ctx(request, "/asset", asset=asset,
                                                                   class_label=CLASS_LABELS.get(asset.asset_class, ""),
                                                                   concepts=concepts[:8], ai_ready=st.gemini.configured))


@router.get("/health", response_class=HTMLResponse)
def health_page(request: Request) -> HTMLResponse:
    from app.api.system import health_snapshot

    return templates.TemplateResponse(request, "health.html", _ctx(request, "/health", h=health_snapshot(request)))

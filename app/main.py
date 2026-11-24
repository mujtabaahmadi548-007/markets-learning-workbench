"""FastAPI application factory: builds every service and schedules the background jobs."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai.gemini_client import AIError, GeminiService
from app.api import learn, market, misc, news, pages, system
from app.config import PROJECT_ROOT, Settings, get_settings
from app.db.migrations import init_db
from app.db.session import Database
from app.logging_setup import setup_logging
from app.net import setup_tls
from app.providers.base import make_http_client
from app.providers.registry import build_providers
from app.services.alerts import AlertService
from app.services.assets import load_assets, seed_assets
from app.services.breakdowns import BreakdownService
from app.services.briefing import BriefingService
from app.services.calendar import CalendarService
from app.services.candles import CandleService
from app.services.explainer import MoveExplainer
from app.services.feeds import FeedManager
from app.services.learning import LearningService, seed_learning
from app.services.news import NewsService
from app.services.scheduler import JobScheduler, prune_snapshots
from app.services.settings_store import load_overrides
from app.timeutil import JAKARTA
from app.web import APP_VERSION, AppState, templates

log = logging.getLogger("app")


def _soon(seconds: int) -> datetime:
    return datetime.now(JAKARTA) + timedelta(seconds=seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_tls()
    setup_logging(settings.log_dir, settings.secret_values())
    # Tests set MLW_NO_FEEDS=1: everything is built, but no network connections are opened.
    start_network = os.environ.get("MLW_NO_FEEDS") != "1"
    # Website build (build_site.py): prices stream while the build runs, but no scheduled jobs.
    build_mode = os.environ.get("MLW_BUILD") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        db = Database(settings.database_path)
        version = init_db(db)
        load_overrides(db, settings)
        seed_assets(db)
        seed_learning(db)
        assets = load_assets(db)
        http = make_http_client()
        providers = build_providers(settings, http)
        if build_mode and "finnhub" in providers:
            # Finnhub free = one stream per key, used by the app on the user's PC; the website uses REST quotes.
            providers["finnhub"].stream_enabled = False
        feeds = FeedManager(db, providers, assets)
        candles = CandleService(db, providers, feeds.health)
        gemini = GeminiService(settings, db)
        symbols = {a.symbol: a.name for a in assets if a.enabled}
        news_svc = NewsService(db, http, gemini, symbols, settings.finnhub_api_key, move_lookup=feeds.change_pct_for)
        breakdowns = BreakdownService(db, gemini, news_svc, http, symbols)
        learning_svc = LearningService(db, gemini, news_svc)
        calendar = CalendarService(db, http, settings.allow_unofficial_sources)
        alerts = AlertService(db, feeds)
        feeds.on_update.append(alerts.check)
        state = AppState(settings=settings, db=db, gemini=gemini, schema_version=version, feeds=feeds, candles=candles,
                         http=http, news=news_svc, breakdowns=breakdowns, learning=learning_svc, calendar=calendar, alerts=alerts,
                         explainer=MoveExplainer(db, gemini, news_svc, feeds))
        state.briefing = BriefingService(db, gemini, feeds, news_svc, calendar, learning_svc, breakdowns)
        app.state.mlw = state

        def on_news(n: int) -> None:
            if n:
                asyncio.get_running_loop().create_task(feeds.broadcaster.send_all({"type": "news", "new": n}))

        news_svc.listeners.append(on_news)

        if start_network:
            await feeds.start()
        if start_network and not build_mode:
            sched = JobScheduler(db)
            counter = {"n": 0}

            async def spark() -> str:
                await candles.refresh_sparklines(list(feeds.assets.values()))
                return f"{len(candles.spark)} sparklines"

            async def daily() -> str:
                for a in list(feeds.assets.values()):
                    await candles.chart(a, "1Y")
                return "daily candles refreshed"

            async def news_job() -> str:
                counter["n"] += 1
                stats = await news_svc.run(include_google=counter["n"] % 4 == 1, include_finnhub=counter["n"] % 6 == 1)
                return f"{stats.new} new articles"

            async def image_job() -> str:
                return f"{await news_svc.image_pass(limit=120)} article images found"

            async def ai_tag_job() -> str:
                return f"{await news_svc.ai_tag_pass()} articles tagged by AI"

            async def auto_breakdown_job() -> str:
                per_run = max(0, round(settings.auto_breakdowns_per_hour / 3))
                if per_run == 0:
                    return "off"
                return f"{await breakdowns.auto_run(per_run)} breakdowns"

            async def calendar_job() -> str:
                return f"{await calendar.refresh()} calendar events"

            async def briefing_job() -> str:
                await state.briefing.generate(auto=True)
                return "briefing generated"

            async def briefing_retry_job() -> str:
                due, why = state.briefing.watch_retry_due()
                if not due:
                    return why
                return f"briefing summary {await state.briefing.retry_watch()}"

            async def prune() -> str:
                n1 = await asyncio.to_thread(prune_snapshots, db)
                n2 = await asyncio.to_thread(candles.prune)
                n3 = await asyncio.to_thread(news_svc.prune)
                return f"removed {n1} snapshots, {n2} candles, {n3} old articles"

            sched.add("refresh_sparklines", spark, trigger="interval", minutes=5, next_run_time=_soon(3))
            sched.add("refresh_daily_candles", daily, trigger="interval", hours=6, next_run_time=_soon(60))
            sched.add("news_ingest", news_job, trigger="interval", minutes=settings.news_interval_min, next_run_time=_soon(5))
            sched.add("news_images", image_job, trigger="interval", minutes=5, next_run_time=_soon(60))
            sched.add("ai_tagging", ai_tag_job, trigger="interval", minutes=20, next_run_time=_soon(240))
            sched.add("auto_breakdowns", auto_breakdown_job, trigger="interval", minutes=20, next_run_time=_soon(300))
            sched.add("calendar_refresh", calendar_job, trigger="interval", hours=12, next_run_time=_soon(20))
            sched.add("daily_briefing", briefing_job, trigger="cron", hour=7, minute=0)
            sched.add("briefing_summary_retry", briefing_retry_job, trigger="interval", minutes=10, next_run_time=_soon(400))
            sched.add("prune_data", prune, trigger="cron", hour=3, minute=30)
            sched.start()
            state.scheduler = sched
        log.info("Markets Learning Workbench %s started (db schema v%s, %d assets)", APP_VERSION, version, len(assets))
        try:
            yield
        finally:
            if state.scheduler:
                state.scheduler.shutdown()
            if start_network:
                await feeds.stop()
            await http.aclose()
            db.dispose()
            log.info("Shut down")

    app = FastAPI(title="Markets Learning Workbench", version=APP_VERSION, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
    for r in (pages.router, system.router, market.router, news.router, learn.router, misc.router):
        app.include_router(r)

    @app.exception_handler(AIError)
    async def ai_error(request: Request, exc: AIError):  # noqa: ANN202
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):  # noqa: ANN202
        if request.url.path.startswith("/api/") or exc.status_code != 404:
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return HTMLResponse(templates.get_template("error.html").render(
            message=str(exc.detail), version=APP_VERSION, nav=pages.NAV, current_stage=pages.CURRENT_STAGE, page=""),
            status_code=404)

    return app

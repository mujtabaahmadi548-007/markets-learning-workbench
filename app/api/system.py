"""System JSON endpoints: ping and health."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import state
from app.models.tables import JobRun
from app.timeutil import gemini_next_reset, utcnow

router = APIRouter(prefix="/api")


@router.get("/ping")
def ping() -> dict[str, Any]:
    return {"ok": True, "server_time_utc": utcnow().isoformat()}


def health_snapshot(request: Request) -> dict[str, Any]:
    """Everything /health shows. Never includes key values (only set / missing)."""
    st = state(request)
    providers = st.feeds.provider_views()  # live in-memory status (also saved to provider_status every 10 s)
    with st.db.session() as s:
        jobs = [
            {
                "job": j.job_name,
                "last_status": j.last_status,
                "last_started": j.last_started.isoformat() if j.last_started else None,
                "last_finished": j.last_finished.isoformat() if j.last_finished else None,
                "last_error": j.last_error,
                "run_count": j.run_count,
            }
            for j in s.scalars(select(JobRun).order_by(JobRun.job_name))
        ]
    return {
        "server_time_utc": utcnow().isoformat(),
        "schema_version": st.schema_version,
        "database_bytes": st.db.size_bytes(),
        "keys": st.settings.key_status(),
        "gemini": {
            "configured": st.gemini.configured,
            "model_fast": st.settings.model_fast or None,
            "model_smart": st.settings.model_smart or None,
            "calls_today": st.gemini.calls_today(),
            "daily_limit": st.settings.gemini_daily_call_limit,
            "next_reset_utc": gemini_next_reset().isoformat(),
        },
        "providers": providers,
        "jobs": jobs,
        "news_feeds": st.news.feed_status() if st.news else [],
        "news_last_run": st.news.last_run.isoformat() if st.news and st.news.last_run else None,
        "calendar_status": st.calendar.status if st.calendar else {},
        "lan_access": st.settings.lan_access,
        "allow_unofficial_sources": st.settings.allow_unofficial_sources,
    }


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return health_snapshot(request)

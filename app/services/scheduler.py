"""Scheduled jobs (APScheduler). Every run is recorded in job_runs (shown on /health).
A failing job is logged and retried at its next run; it never stops the app.

Stage 1 jobs:
- refresh_sparklines  every 5 min    small 1-day charts on the dashboard
- refresh_daily       every 6 h      daily candles for every asset (6M / 1Y charts)
- prune_data          daily 03:30 WIB  thin out old price snapshots and fine-grained candles
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select

from app.db.session import Database
from app.models.tables import JobRun, PriceSnapshot
from app.timeutil import JAKARTA, utcnow

log = logging.getLogger(__name__)


def record_job(db: Database, name: str, status: str, error: str | None = None, started: bool = False) -> None:
    with db.session() as s:
        row = s.get(JobRun, name) or JobRun(job_name=name, run_count=0)
        if started:
            row.last_started, row.last_status = utcnow(), "running"
        else:
            row.last_finished, row.last_status, row.last_error = utcnow(), status, error
            row.run_count = (row.run_count or 0) + 1
        s.merge(row)


def prune_snapshots(db: Database) -> int:
    """Keep 1-minute snapshots for 8 days; after that keep only the first one of each hour (for 400 days)."""
    now = utcnow()
    with db.session() as s:
        keep_hourly = (
            select(func.min(PriceSnapshot.id))
            .where(PriceSnapshot.source_timestamp < now - timedelta(days=8))
            .group_by(PriceSnapshot.asset_id, func.strftime("%Y-%m-%d %H", PriceSnapshot.source_timestamp))
        )
        r1 = s.execute(delete(PriceSnapshot).where(
            PriceSnapshot.source_timestamp < now - timedelta(days=8), PriceSnapshot.id.not_in(keep_hourly)))
        r2 = s.execute(delete(PriceSnapshot).where(PriceSnapshot.source_timestamp < now - timedelta(days=400)))
    return (r1.rowcount or 0) + (r2.rowcount or 0)


class JobScheduler:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=JAKARTA)

    def add(self, name: str, func_: Callable[[], Awaitable[object]], **trigger) -> None:  # noqa: ANN003
        async def run() -> None:
            import asyncio

            await asyncio.to_thread(record_job, self.db, name, "running", None, True)
            try:
                result = await func_()
                await asyncio.to_thread(record_job, self.db, name, "ok")
                log.info("Job %s finished%s", name, f" ({result})" if result is not None else "")
            except Exception as e:  # noqa: BLE001
                log.exception("Job %s failed", name)
                await asyncio.to_thread(record_job, self.db, name, "error", f"{type(e).__name__}: {e}"[:300])

        self.scheduler.add_job(run, id=name, name=name, replace_existing=True, max_instances=1, coalesce=True,
                               misfire_grace_time=300, **trigger)

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

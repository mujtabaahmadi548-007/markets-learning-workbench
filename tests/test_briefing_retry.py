"""A failed 'what to watch' is retried quickly at first, then hourly, and not at all while both AI models are out
of their daily free quota (so a bad day cannot use up the website's AI budget)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.db.migrations import init_db
from app.db.session import Database
from app.services.briefing import BriefingService
from app.timeutil import utcnow


class StubGemini:
    def __init__(self) -> None:
        self.configured = True
        self.settings = SimpleNamespace(model_smart="smart-m", model_fast="fast-m")
        self.out: set[str] = set()

    def day_quota_used_up(self, model: str) -> bool:
        return model in self.out


def make(tmp_path: Path) -> tuple[BriefingService, StubGemini]:
    db = Database(tmp_path / "b.db")
    init_db(db)
    g = StubGemini()
    return BriefingService(db, g, None, None, None, None, None), g


def save(svc: BriefingService, **extra) -> None:  # noqa: ANN003
    c = {"generated_at": utcnow().isoformat(), "moves": [], "stories": [], "events": [], "review": [],
         "what_to_watch": [], "watch_note": "x", "watch_failed": True, **extra}
    svc._save(date(2026, 11, 24), c)


def test_retry_spacing_and_quota_pause(tmp_path: Path) -> None:
    svc, g = make(tmp_path)
    save(svc)
    assert svc.watch_retry_due() == (True, "")  # first retry right away
    recent = (utcnow() - timedelta(minutes=5)).isoformat()
    save(svc, watch_attempts=1, watch_retry_at=recent)
    assert svc.watch_retry_due() == (False, "next try within 10 min")
    save(svc, watch_attempts=1, watch_retry_at=(utcnow() - timedelta(minutes=11)).isoformat())
    assert svc.watch_retry_due()[0]
    # after 3 tries: hourly
    save(svc, watch_attempts=3, watch_retry_at=(utcnow() - timedelta(minutes=30)).isoformat())
    assert svc.watch_retry_due() == (False, "next try within 60 min")
    save(svc, watch_attempts=3, watch_retry_at=(utcnow() - timedelta(minutes=61)).isoformat())
    assert svc.watch_retry_due()[0]
    # only the smart model out of quota: still retried (the fast model takes over)
    g.out = {"smart-m"}
    assert svc.watch_retry_due()[0]
    # both out: nothing is sent until Google's daily reset
    g.out = {"smart-m", "fast-m"}
    due, why = svc.watch_retry_due()
    assert not due and "daily reset" in why
    # a written summary is never retried
    g.out = set()
    save(svc, what_to_watch=["a", "b", "c"], watch_failed=False)
    assert svc.watch_retry_due() == (False, "nothing to retry")

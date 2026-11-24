"""Gemini wrapper tests against a local fake server (no real key, nothing sent to Google)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.gemini_client import (
    AIDailyLimitReached,
    AIError,
    AINotConfigured,
    AIQuotaExceeded,
    GeminiService,
    pick_recommended_models,
)
from app.config import load_settings
from app.db.migrations import init_db
from app.db.session import Database
from app.models.tables import AICall

FAKE_KEY = "FAKEKEY-for-local-tests-only-9876"


class CountAnswer(BaseModel):
    speakers: int | None
    answer_basis: str


def make_service(tmp_path: Path, monkeypatch, fake, limit: int = 300) -> GeminiService:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_MODEL_SMART", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_DAILY_CALL_LIMIT", str(limit))
    monkeypatch.setenv("GEMINI_BASE_URL", fake.base_url)
    settings = load_settings(env_file=None)
    db = Database(tmp_path / "g.db")
    init_db(db)
    return GeminiService(settings, db)


def test_not_configured_without_key(tmp_path: Path) -> None:
    svc = GeminiService(load_settings(env_file=None), None)
    assert not svc.configured
    with pytest.raises(AINotConfigured):
        svc.generate("x", "hi", model="m")


def test_text_call_is_logged_and_key_not_in_url(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    res = svc.generate("connection_test", "Reply OK", tier="fast")
    assert res.text == "OK" and res.model == "gemini-3.5-flash-lite"
    assert res.tokens_in == 12 and res.tokens_out == 3
    req = fake_gemini.requests[-1]
    assert FAKE_KEY not in req["path"]  # key goes in a header, never in the URL
    assert "gemini-3.5-flash-lite:generateContent" in req["path"]
    assert "Use ONLY the data provided" in str(req["body"].get("systemInstruction"))
    with svc.db.session() as s:
        calls = s.scalars(select(AICall)).all()
    assert len(calls) == 1 and calls[0].success and calls[0].purpose == "connection_test"
    assert svc.calls_today() == 1


def test_structured_json_is_validated(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    res = svc.generate("json", "q", tier="smart", schema=CountAnswer)
    assert isinstance(res.parsed, CountAnswer) and res.parsed.speakers == 3
    body = fake_gemini.requests[-1]["body"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_wrong_json_shape_is_rejected(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.mode = "bad_json"
    with pytest.raises(AIError):
        svc.generate("json", "q", schema=CountAnswer)


def test_quota_429_raises_without_long_wait(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.mode = "429"
    with pytest.raises(AIQuotaExceeded):
        svc.generate("x", "q", tier="fast")
    with svc.db.session() as s:
        call = s.scalars(select(AICall)).one()
    assert call.success is False and "429" in (call.error or "")


def test_overloaded_smart_model_falls_back_to_fast(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.fail_models = {"gemini-3.8-flash:"}
    res = svc.generate("x", "q", tier="smart")
    assert res.model == "gemini-3.5-flash-lite" and res.text == "OK"
    with svc.db.session() as s:
        calls = s.scalars(select(AICall).order_by(AICall.id)).all()
    assert [c.success for c in calls] == [False, True]


def test_smart_model_out_of_daily_quota_uses_fast_and_remembers(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    """Free-tier quotas are per model: when the smart one is used up for the day, the fast one finishes the job,
    and later calls (even from a new process, e.g. the next website build) go straight to the fast model."""
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.day_quota_models = {"gemini-3.8-flash:"}
    res = svc.generate("briefing", "q", tier="smart")
    assert res.model == "gemini-3.5-flash-lite" and res.text == "OK"
    assert svc.day_quota_used_up("gemini-3.8-flash") and not svc.day_quota_used_up("gemini-3.5-flash-lite")
    n = len(fake_gemini.requests)
    fresh = GeminiService(svc.settings, svc.db)  # a new process reads the call log
    assert fresh.generate("why", "q", tier="smart").model == "gemini-3.5-flash-lite"
    assert len(fake_gemini.requests) == n + 1 and "gemini-3.5-flash-lite:" in fake_gemini.requests[-1]["path"]
    # an explicitly chosen model is never swapped
    with pytest.raises(AIQuotaExceeded):
        svc.generate("x", "q", model="gemini-3.8-flash")


def test_per_minute_429_is_not_a_daily_quota(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    from app.ai.gemini_client import is_daily_quota_error
    assert not is_daily_quota_error("HTTP 429: Resource has been exhausted (requests per minute)")
    assert is_daily_quota_error("HTTP 429: ... description: Generate content requests per day limit exceeded on Free Tier.")


def test_daily_limit_blocks_before_calling(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini, limit=1)
    svc.generate("x", "q", tier="fast")
    n = len(fake_gemini.requests)
    with pytest.raises(AIDailyLimitReached):
        svc.generate("x", "q", tier="fast")
    assert len(fake_gemini.requests) == n  # nothing was sent


def test_list_models_and_recommendation(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    svc = make_service(tmp_path, monkeypatch, fake_gemini)
    models = svc.list_models()
    names = {m["name"] for m in models}
    assert "gemini-3.8-flash" in names
    rec = pick_recommended_models(models)
    assert rec["fast"] == "gemini-3.5-flash-lite"
    assert rec["smart"] == "gemini-3.8-flash"  # preview and TTS models are skipped

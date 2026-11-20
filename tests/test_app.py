from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.timeutil import fmt_jakarta, gemini_day_start

FAKE_KEY = "FAKEKEY-app-test-ZYXWVU5678"


def client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(create_app())


def test_pages_render_with_footer() -> None:
    with client() as c:
        for path in ("/", "/health"):
            r = c.get(path)
            assert r.status_code == 200
            assert "Educational tool — not financial advice." in r.text
        assert c.get("/api/ping").json()["ok"] is True
        h = c.get("/api/health").json()
        assert h["schema_version"] >= 1
        assert h["gemini"]["configured"] is False
        assert h["keys"]["GEMINI_API_KEY"] is False


def test_key_value_never_shown(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    with client() as c:
        for path in ("/", "/health", "/api/health"):
            r = c.get(path)
            assert FAKE_KEY not in r.text
            assert FAKE_KEY[-4:] not in r.text  # not even the hint in the web UI
        assert c.get("/api/health").json()["keys"]["GEMINI_API_KEY"] is True


def test_no_numbers_invented_on_dashboard() -> None:
    """Stage 0 shows no market data at all (no price-like numbers on the page)."""
    with client() as c:
        html = c.get("/").text
    assert "$" not in html and "USD" not in html


def test_jakarta_time_formatting() -> None:
    assert fmt_jakarta(datetime(2026, 11, 19, 20, 15, tzinfo=timezone.utc)) == "20 Nov 2026, 3:15 AM"


def test_gemini_day_starts_midnight_pacific() -> None:
    # 20 Nov 2026 03:00 UTC is still 19 Nov in California (PST, UTC-8) -> day began 19 Nov 08:00 UTC.
    start = gemini_day_start(datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)
    # Summer (PDT, UTC-7)
    start = gemini_day_start(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)

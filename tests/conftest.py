"""Test setup: never load the real .env, never touch the real database or logs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Must be set before anything reads settings.
os.environ["MLW_NO_DOTENV"] = "1"
os.environ["MLW_NO_FEEDS"] = "1"  # the app builds everything but opens no network connections
for _k in ("GEMINI_API_KEY", "FINNHUB_API_KEY", "TWELVE_DATA_API_KEY", "ALPHA_VANTAGE_API_KEY", "COINGECKO_API_KEY",
           "GEMINI_MODEL", "GEMINI_MODEL_FAST", "GEMINI_MODEL_SMART", "GEMINI_BASE_URL"):
    os.environ.pop(_k, None)

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own temp database and log folder."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_gemini():
    from tests.fake_gemini import FakeGemini

    fake = FakeGemini().start()
    yield fake
    fake.stop()

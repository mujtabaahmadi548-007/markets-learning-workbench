"""Application settings, loaded from the .env file in the project root.

Secrets (API keys) are kept in this object only. Nothing in the app prints or logs them;
use `mask_secret()` when a hint is needed (it shows only the last 4 characters).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Names of settings that hold secrets. The logging filter redacts their values everywhere.
SECRET_FIELDS: tuple[str, ...] = (
    "gemini_api_key",
    "finnhub_api_key",
    "twelve_data_api_key",
    "alpha_vantage_api_key",
    "coingecko_api_key",
)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value and value.strip() else default
    except ValueError:
        return default


def _str(value: str | None) -> str:
    return (value or "").strip()


class Settings(BaseModel):
    """All runtime settings. Built by `load_settings()`; never construct with real keys in tests."""

    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = ""
    gemini_model_fast: str = ""
    gemini_model_smart: str = ""
    gemini_daily_call_limit: int = 300
    # Advanced: force the API version ("v1" or "v1beta"). Empty = the SDK's default.
    gemini_api_version: str = ""
    # Only for tests / a local fake server. Never set in normal use.
    gemini_base_url: str = ""

    finnhub_api_key: str = Field(default="", repr=False)
    twelve_data_api_key: str = Field(default="", repr=False)
    alpha_vantage_api_key: str = Field(default="", repr=False)
    coingecko_api_key: str = Field(default="", repr=False)

    port: int = 8000
    lan_access: bool = False
    allow_unofficial_sources: bool = True

    # Adjustable on the Settings page (saved in the database, override these defaults)
    news_interval_min: int = 5
    auto_breakdowns_per_hour: int = 3

    database_path: Path = PROJECT_ROOT / "data" / "markets.db"
    log_dir: Path = PROJECT_ROOT / "logs"

    @property
    def host(self) -> str:
        return "0.0.0.0" if self.lan_access else "127.0.0.1"

    @property
    def model_fast(self) -> str:
        """Model for bulk/cheap work. Falls back to GEMINI_MODEL."""
        return self.gemini_model_fast or self.gemini_model

    @property
    def model_smart(self) -> str:
        """Model for breakdowns/explanations. Falls back to GEMINI_MODEL."""
        return self.gemini_model_smart or self.gemini_model

    def secret_values(self) -> list[str]:
        """All non-empty secret values (used by the log redaction filter)."""
        return [v for v in (getattr(self, f) for f in SECRET_FIELDS) if v]

    def key_status(self) -> dict[str, bool]:
        """Which keys are set. Safe to show in the UI: True/False only, never the value."""
        return {
            "GEMINI_API_KEY": bool(self.gemini_api_key),
            "FINNHUB_API_KEY": bool(self.finnhub_api_key),
            "TWELVE_DATA_API_KEY": bool(self.twelve_data_api_key),
            "ALPHA_VANTAGE_API_KEY": bool(self.alpha_vantage_api_key),
            "COINGECKO_API_KEY": bool(self.coingecko_api_key),
        }


def mask_secret(value: str) -> str:
    """Show only the last 4 characters of a secret, e.g. '…a1B2'. Empty -> 'missing'."""
    if not value:
        return "missing"
    if len(value) <= 8:
        return "set (too short to show a hint)"
    return f"set (ends with …{value[-4:]})"


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Read .env (if present) without overriding variables already set in the environment.

    Tests set MLW_NO_DOTENV=1 so your real .env (and your real keys) are never loaded.
    """
    if os.environ.get("MLW_NO_DOTENV") == "1":
        env_file = None
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False, encoding="utf-8")
    env = os.environ
    db_path = _str(env.get("DATABASE_PATH"))
    log_dir = _str(env.get("LOG_DIR"))
    return Settings(
        gemini_api_key=_str(env.get("GEMINI_API_KEY")),
        gemini_model=_str(env.get("GEMINI_MODEL")),
        gemini_model_fast=_str(env.get("GEMINI_MODEL_FAST")),
        gemini_model_smart=_str(env.get("GEMINI_MODEL_SMART")),
        gemini_daily_call_limit=_int(env.get("GEMINI_DAILY_CALL_LIMIT"), 300),
        gemini_api_version=_str(env.get("GEMINI_API_VERSION")),
        gemini_base_url=_str(env.get("GEMINI_BASE_URL")),
        finnhub_api_key=_str(env.get("FINNHUB_API_KEY")),
        twelve_data_api_key=_str(env.get("TWELVE_DATA_API_KEY")),
        alpha_vantage_api_key=_str(env.get("ALPHA_VANTAGE_API_KEY")),
        coingecko_api_key=_str(env.get("COINGECKO_API_KEY")),
        port=_int(env.get("PORT"), 8000),
        lan_access=_bool(env.get("LAN_ACCESS"), False),
        allow_unofficial_sources=_bool(env.get("ALLOW_UNOFFICIAL_SOURCES"), True),
        database_path=Path(db_path) if db_path else PROJECT_ROOT / "data" / "markets.db",
        log_dir=Path(log_dir) if log_dir else PROJECT_ROOT / "logs",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached). Tests call `get_settings.cache_clear()` after changing env vars."""
    return load_settings()

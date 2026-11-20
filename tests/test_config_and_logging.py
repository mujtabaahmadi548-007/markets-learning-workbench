from __future__ import annotations

import logging
from pathlib import Path

from app.config import load_settings, mask_secret
from app.logging_setup import redact, setup_logging

FAKE_KEY = "TESTKEY-not-real-ABCDEFGH1234"


def test_settings_defaults(monkeypatch) -> None:
    s = load_settings(env_file=None)
    assert s.port == 8000
    assert s.lan_access is False
    assert s.host == "127.0.0.1"
    assert s.gemini_daily_call_limit == 300
    assert s.allow_unofficial_sources is True
    assert all(v is False for v in s.key_status().values())


def test_lan_access_binds_all_interfaces(monkeypatch) -> None:
    monkeypatch.setenv("LAN_ACCESS", "true")
    assert load_settings(env_file=None).host == "0.0.0.0"


def test_env_file_is_read(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MLW_NO_DOTENV", raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_MODEL_FAST=model-a\nGEMINI_DAILY_CALL_LIMIT=42\nPORT=8123\n", encoding="utf-8")
    for k in ("GEMINI_MODEL_FAST", "GEMINI_DAILY_CALL_LIMIT", "PORT"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings(env_file=env)
    assert s.model_fast == "model-a"
    assert s.gemini_daily_call_limit == 42
    assert s.port == 8123
    # load_dotenv wrote straight into os.environ; remove those values so other tests are unaffected.
    import os

    for k in ("GEMINI_MODEL_FAST", "GEMINI_DAILY_CALL_LIMIT", "PORT"):
        os.environ.pop(k, None)


def test_model_fallback_to_default(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    s = load_settings(env_file=None)
    assert s.model_fast == "base-model" and s.model_smart == "base-model"


def test_key_never_in_repr(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    s = load_settings(env_file=None)
    assert FAKE_KEY not in repr(s)
    assert FAKE_KEY not in str(s.key_status())
    assert mask_secret(FAKE_KEY) == "set (ends with …1234)"
    assert mask_secret("") == "missing"


def test_redaction_of_values_and_query_params(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs", [FAKE_KEY])
    assert FAKE_KEY not in redact(f"calling with {FAKE_KEY}")
    assert "abcdef123456" not in redact("GET https://x.test/q?apikey=abcdef123456&s=AAPL")
    assert "abcdef123456" not in redact("wss://ws.finnhub.io?token=abcdef123456")


def test_secret_never_reaches_log_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir, [FAKE_KEY])
    log = logging.getLogger("test")
    log.error("request failed for key %s", FAKE_KEY)
    try:
        raise RuntimeError(f"boom {FAKE_KEY}")
    except RuntimeError:
        log.exception("with traceback")
    for h in logging.getLogger().handlers:
        h.flush()
    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "request failed" in text and "[REDACTED]" in text
    assert FAKE_KEY not in text

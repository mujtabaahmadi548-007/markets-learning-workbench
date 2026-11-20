"""Logging to logs/app.log (rotating) and the console, with secrets redacted.

Two layers of protection:
1. HTTP client loggers (httpx/httpcore) are kept at WARNING so request URLs (which can carry
   `apikey=` / `token=` query parameters) are never written.
2. A filter on every handler replaces any known secret value, and anything that looks like
   `apikey=...`, `token=...`, `key=...`, with [REDACTED].
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    re.compile(r"(?i)((?:api[_-]?key|apikey|token|access[_-]?key|x-goog-api-key|key)\s*[=:]\s*[\"']?)([A-Za-z0-9_\-\.]{8,})"),
]

_secret_values: list[str] = []


def redact(text: str) -> str:
    """Remove secret values and key-like query parameters from any text."""
    if not text:
        return text
    for value in _secret_values:
        if value and value in text:
            text = text.replace(value, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the app
            return True
        clean = redact(message)
        if record.exc_info:
            # Render the traceback now so it can be redacted too.
            formatter = logging.Formatter()
            exc_text = formatter.formatException(record.exc_info)
            record.exc_text = redact(exc_text)
            record.exc_info = None
        record.msg = clean
        record.args = None
        return True


class QuietWindowsResets(logging.Filter):
    """Drop Windows' harmless 'connection forcibly closed' (WinError 10054) noise that asyncio logs whenever a
    browser tab closes its live connection (e.g. when you open another page)."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in record.getMessage():
            return False
        return True


def setup_logging(log_dir: Path, secrets: list[str], level: int = logging.INFO) -> None:
    """Configure root logging once. Safe to call again (replaces our handlers)."""
    global _secret_values
    _secret_values = sorted({s for s in secrets if s}, key=len, reverse=True)

    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_mlw", False):
            root.removeHandler(handler)

    file_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
        handler.addFilter(RedactingFilter())
        handler._mlw = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level)

    asyncio_log = logging.getLogger("asyncio")
    if not any(isinstance(f, QuietWindowsResets) for f in asyncio_log.filters):
        asyncio_log.addFilter(QuietWindowsResets())

    # Never log HTTP request lines (they may contain keys in query strings).
    for noisy in ("httpx", "httpcore", "httpx2", "httpcore2", "websockets", "google_genai", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

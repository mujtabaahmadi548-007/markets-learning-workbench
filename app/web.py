"""Shared web objects: version string, Jinja templates, and the per-app state container."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.ai.gemini_client import GeminiService
from app.config import PROJECT_ROOT, Settings
from app.db.session import Database
from app.timeutil import fmt_jakarta, fmt_utc

if TYPE_CHECKING:
    import httpx

    from app.services.candles import CandleService
    from app.services.feeds import FeedManager
    from app.services.scheduler import JobScheduler

APP_VERSION = "1.1.0"

def autolink(text: str | None, concepts: list[dict] | None) -> Markup:
    """Escape text and link the first mention of each concept name to its glossary entry."""
    s = str(escape(text or ""))
    links: list[str] = []
    for c in sorted(concepts or [], key=lambda c: -len(c.get("name", ""))):
        name = str(escape(re.sub(r"\s*\(.*?\)\s*$", "", c.get("name", "")).strip()))
        if len(name) < 3:
            continue
        pat = re.compile(rf"(?<![\w/\-\x00])({re.escape(name)})(?![\w\-])", re.I)

        def repl(m: re.Match, c=c) -> str:  # noqa: ANN001
            links.append(f'<a class="concept-link" href="/glossary/{escape(c["slug"])}">{m.group(1)}</a>')
            return f"\x00{len(links) - 1}\x00"

        s = pat.sub(repl, s, count=1)
    s = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], s)
    return Markup(s)


_ASSET_VERSIONS: dict[str, tuple[int, int, str]] = {}


def asset_url(path: str) -> str:
    """/static/<path>?v=<short hash of the file's content>.

    The address changes only when the file's content changes, so browsers fetch a fresh copy after an update and
    keep using their saved copy otherwise. (A modified-time version changed on every website build, because each
    GitHub build checks the files out anew, so phones re-downloaded every script and stylesheet every 10 minutes.)
    """
    f = PROJECT_ROOT / "static" / path
    try:
        info = f.stat()
        known = _ASSET_VERSIONS.get(path)
        if known and known[0] == info.st_mtime_ns and known[1] == info.st_size:
            v = known[2]
        else:
            v = hashlib.sha256(f.read_bytes()).hexdigest()[:10]
            _ASSET_VERSIONS[path] = (info.st_mtime_ns, info.st_size, v)
    except OSError:
        v = "0"
    return f"/static/{path}?v={v}"


import os as _os

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
templates.env.globals["static_url"] = asset_url
# Website build (GitHub Pages): MLW_SITE_BASE is the site's path, e.g. "/markets-learning-workbench".
templates.env.globals["site"] = {"static": _os.environ.get("MLW_SITE_BUILD") == "1",
                                 "base": _os.environ.get("MLW_SITE_BASE", "").rstrip("/")}
templates.env.filters["jakarta"] = fmt_jakarta
templates.env.filters["utc"] = fmt_utc
templates.env.filters["autolink"] = autolink


@dataclass
class AppState:
    settings: Settings
    db: Database
    gemini: GeminiService
    schema_version: int
    feeds: "FeedManager"
    candles: "CandleService"
    scheduler: "JobScheduler | None" = None
    http: "httpx.AsyncClient | None" = None
    news: Any = None
    breakdowns: Any = None
    explainer: Any = None
    learning: Any = None
    calendar: Any = None
    briefing: Any = None
    alerts: Any = None

"""Every page and main API route renders (no network, no AI: MLW_NO_FEEDS=1, no key)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

PAGES = ["/", "/news", "/learn", "/glossary", "/quiz", "/briefing", "/alerts", "/demo", "/settings", "/health",
         "/asset/BTC", "/asset/XAU", "/asset/US10Y", "/glossary/inflation"]
APIS = ["/api/quotes", "/api/news", "/api/news/sources", "/api/sparklines", "/api/providers", "/api/health",
        "/api/quiz/next?mode=today", "/api/quiz/next?mode=review", "/api/quiz/stats", "/api/alerts", "/api/calendar",
        "/api/assets/BTC", "/api/assets/BTC/news", "/api/assets/BTC/why"]


def test_all_pages_render() -> None:
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        for p in PAGES:
            r = c.get(p)
            assert r.status_code == 200, (p, r.text[:500])
            assert "Educational tool — not financial advice." in r.text, p
        lesson = c.get("/learn").text
        import re

        first = re.search(r'href="/learn/(\d+)"', lesson).group(1)
        assert c.get(f"/learn/{first}").status_code == 200
        for a in APIS:
            r = c.get(a)
            assert r.status_code == 200, (a, r.text[:300])
        assert c.get("/asset/NOPE").status_code == 404
        assert c.get("/api/assets/NOPE").status_code == 404


def test_briefing_live_crypto_only_on_newest_briefing() -> None:
    from datetime import date

    get_settings.cache_clear()
    moves = [
        {"symbol": "BTC", "name": "Bitcoin", "asset_class": "crypto", "decimals": 2, "currency": "USD", "price": 60000.0,
         "change": 600.0, "change_pct": 1.0, "status": "LIVE", "source": "Binance", "kind": "stream",
         "source_timestamp": "2026-11-21T08:06:37+00:00"},
        {"symbol": "SPX", "name": "S&P 500 index", "asset_class": "index", "decimals": 2, "currency": "USD", "price": 6910.39,
         "change": -265.44, "change_pct": -3.7, "status": "MARKET_CLOSED", "source": "Yahoo Finance (unofficial)",
         "kind": "poll", "source_timestamp": "2026-11-20T21:35:54+00:00", "newer_note": "FRED ends on 19 Nov."},
        {"symbol": "US10Y", "name": "US 10-year Treasury yield", "asset_class": "macro", "decimals": 2, "currency": "%",
         "price": 4.92, "change": -0.12, "change_pct": None, "status": "DELAYED", "source": "US Treasury", "kind": "daily",
         "source_timestamp": "2026-11-20T00:00:00+00:00"},
    ]
    content = {"generated_at": "2026-11-21T08:07:27+00:00", "moves": moves, "stories": [], "events": [],
               "what_to_watch": [], "watch_note": "", "watch_validated": True, "review": []}
    with TestClient(create_app()) as c:
        st = c.app.state.mlw
        st.briefing._save(date(2026, 11, 20), content)
        st.briefing._save(date(2026, 11, 21), content)
        newest = c.get("/briefing").text
        assert '<tr data-symbol="BTC" data-live data-ts="2026-11-21T08:06:37+00:00">' in newest
        assert 'data-symbol="SPX" data-live' not in newest and 'title="FRED ends on 19 Nov."' in newest
        assert "value for <time" in newest and "data-daily" in newest and "▼ −3.70%" in newest
        assert c.get("/briefing?day=2026-11-21").text.count(" data-live data-ts=") == 1
        older = c.get("/briefing?day=2026-11-20").text
        assert " data-live data-ts=" not in older and "Prices at briefing time" in older


def test_ai_endpoints_explain_missing_key() -> None:
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        r = c.post("/api/assets/BTC/why")
        assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["error"]


def test_alert_and_watchlist_roundtrip() -> None:
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        r = c.post("/api/alerts", json={"symbol": "BTC", "condition": "above", "value": 1_000_000})
        assert r.status_code == 200
        assert any(a["symbol"] == "BTC" for a in c.get("/api/alerts").json()["alerts"])
        c.delete(f"/api/alerts/{r.json()['id']}")
        assert c.post("/api/watchlist/XRP/enabled?enabled=false").status_code == 200
        assert all(q["symbol"] != "XRP" for q in c.get("/api/quotes").json()["quotes"])
        assert c.post("/api/watchlist/XRP/enabled?enabled=true").status_code == 200
        assert any(q["symbol"] == "XRP" for q in c.get("/api/quotes").json()["quotes"])
        assert c.post("/api/settings", json={"news_interval_min": 1}).status_code == 400
        assert c.post("/api/settings", json={"auto_breakdowns_per_hour": 2}).status_code == 200

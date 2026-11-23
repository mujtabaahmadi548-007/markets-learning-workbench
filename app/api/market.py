"""Market data JSON API and the live WebSocket for browsers."""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.api.deps import state
from app.services.assets import CLASS_LABELS
from app.services.candles import ALL_RANGES, HISTORY_RANGES
from app.timeutil import utcnow

router = APIRouter()


def _asset_or_404(request: Request, symbol: str):  # noqa: ANN202
    feeds = state(request).feeds
    for a in feeds.assets.values():
        if a.symbol.upper() == symbol.upper():
            return a
    raise HTTPException(404, f"Unknown or disabled asset: {symbol}")


@router.get("/api/quotes")
def quotes(request: Request) -> dict:
    return {"server_time": utcnow().isoformat(), "quotes": state(request).feeds.views(), "class_labels": CLASS_LABELS}


@router.get("/api/assets/{symbol}")
def asset_detail(request: Request, symbol: str) -> dict:
    st = state(request)
    a = _asset_or_404(request, symbol)
    feeds = st.feeds
    sources = []
    active = feeds.active.get(a.id)
    for e in a.quote_chain:
        p = feeds.providers.get(e.provider)
        h = feeds.health.get(e.provider)
        sources.append({
            "provider": e.provider, "label": p.label if p else e.provider, "symbol": e.symbol, "note": e.note,
            "proxy": e.proxy, "unofficial": bool(p and p.unofficial),
            "available": p is not None and p.configured,
            "why_not": None if p is not None and p.configured else ("needs an API key" if p else "unofficial sources are off"),
            "active": bool(active and active[1] == e),
            "health": h.status if h else "N/A",
        })
    return {"server_time": utcnow().isoformat(), "quote": feeds.view(a.id), "sources": sources,
            "chart_sources": [{"provider": e.provider, "symbol": e.symbol, "note": e.note, "proxy": e.proxy} for e in a.candle_chain]}


@router.get("/api/candles/{symbol}")
async def candles(request: Request, symbol: str, range: str = "1D") -> dict:  # noqa: A002
    st = state(request)
    a = _asset_or_404(request, symbol)
    if range not in ALL_RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(ALL_RANGES)}")
    if range in HISTORY_RANGES:
        return await st.candles.history_chart(a, range)
    return await st.candles.chart(a, range)


@router.get("/api/assets/{symbol}/performance")
async def performance(request: Request, symbol: str) -> dict:
    """Returns over 1 month ... 20 years and all history, highest/lowest close, drop from the high."""
    st = state(request)
    return await st.candles.performance(_asset_or_404(request, symbol))


@router.get("/api/sparklines")
def sparklines(request: Request) -> dict:
    return {"sparklines": state(request).candles.spark}


@router.get("/api/providers")
def providers(request: Request) -> dict:
    return {"providers": state(request).feeds.provider_views()}


@router.post("/api/providers/{name}/enabled")
def set_provider(request: Request, name: str, enabled: bool) -> dict:
    """Switch a provider off/on (to test automatic fallback). Saved across restarts."""
    try:
        state(request).feeds.set_provider_enabled(name, enabled)
    except KeyError:
        raise HTTPException(404, f"Unknown provider: {name}") from None
    return {"provider": name, "enabled": enabled}


@router.websocket("/ws")
async def live(ws: WebSocket) -> None:
    await ws.accept()
    feeds = ws.app.state.mlw.feeds
    feeds.broadcaster.clients.add(ws)
    try:
        await ws.send_json({"type": "snapshot", "server_time": utcnow().isoformat(), "quotes": feeds.views()})
        while True:
            # Keep the connection open; the browser may send "ping" text, answered with the server time.
            msg = await asyncio.wait_for(ws.receive_text(), timeout=120)
            if msg == "ping":
                await ws.send_json({"type": "pong", "server_time": utcnow().isoformat()})
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
        pass
    finally:
        feeds.broadcaster.clients.discard(ws)
        with contextlib.suppress(Exception):
            await ws.close()

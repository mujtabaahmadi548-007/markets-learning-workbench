"""Finnhub (free key: FINNHUB_API_KEY) - live US stock trades + quotes.

REST:   https://finnhub.io/api/v1/quote?symbol=AAPL   (real-time quote for US stocks; free)
Stream: wss://ws.finnhub.io?token=KEY  {"type":"subscribe","symbol":"AAPL"}  (trades; free plan: 50 symbols,
        ONE connection per key). Also carries forex/CFD symbols such as OANDA:XAU_USD if your plan allows them.
Free plan: 60 calls/minute, personal use. Stock candles and the economic calendar are paid-only.
The key is sent as a query parameter; it is never logged (HTTP client logs are off, and errors are redacted).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

import websockets

from app.providers.base import OnUpdate, Provider, ProviderError, QuoteData, RateLimiter, fnum

REST = "https://finnhub.io/api/v1"
WS = "wss://ws.finnhub.io"


def parse_quote(d: dict) -> QuoteData:
    price, ts = fnum(d.get("c")), d.get("t")
    if not price or not ts:
        raise ProviderError("Finnhub: no quote for this symbol")
    return QuoteData(
        price=price,
        source_timestamp=datetime.fromtimestamp(int(ts), timezone.utc),
        change=fnum(d.get("d")),
        change_pct=fnum(d.get("dp")),
        prev_close=fnum(d.get("pc")),
        day_high=fnum(d.get("h")),
        day_low=fnum(d.get("l")),
        quote_currency="USD",
    )


def parse_trades(msg: dict) -> dict[str, QuoteData]:
    """A trade message can hold many trades; keep the newest per symbol (price only)."""
    out: dict[str, QuoteData] = {}
    if msg.get("type") != "trade":
        return out
    for t in msg.get("data") or []:
        ts = datetime.fromtimestamp(t["t"] / 1000, timezone.utc)
        prev = out.get(t["s"])
        if prev is None or ts >= prev.source_timestamp:
            out[t["s"]] = QuoteData(price=float(t["p"]), source_timestamp=ts)
    return out


class FinnhubProvider(Provider):
    name = "finnhub"
    label = "Finnhub"
    kind = "stream"
    poll_interval = 60.0  # REST quote refresh for previous close / day range
    expected_delay = 0.0
    requires_key = True

    def __init__(self, http, api_key: str = "") -> None:  # noqa: ANN001
        super().__init__(http, api_key)
        self._limiter = RateLimiter(50)  # stay under the free 60/min

    @property
    def can_stream(self) -> bool:
        return self.configured

    async def get_quote(self, symbol: str) -> QuoteData:
        if not self.configured:
            raise ProviderError("Finnhub: no FINNHUB_API_KEY in .env")
        if ":" in symbol:
            raise ProviderError("Finnhub: REST quotes cover US stocks only; this symbol is stream-only")
        await self._limiter.acquire()
        return parse_quote(await self._get_json(f"{REST}/quote", params={"symbol": symbol, "token": self.api_key}))

    async def stream(self, symbols: set[str], on_update: OnUpdate, on_connected: Callable[[], None]) -> None:
        if not self.configured:
            raise ProviderError("Finnhub: no FINNHUB_API_KEY in .env")
        async with websockets.connect(f"{WS}?token={self.api_key}", open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            for s in sorted(symbols):
                await ws.send(json.dumps({"type": "subscribe", "symbol": s}))
            on_connected()
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "error":
                    raise ProviderError(f"Finnhub stream error: {str(msg.get('msg'))[:120]}")
                for sym, q in parse_trades(msg).items():
                    on_update(sym, q)

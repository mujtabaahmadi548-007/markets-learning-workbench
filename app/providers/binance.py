"""Binance public market data (no key).

Uses Binance's market-data-only endpoints, which are reachable from Indonesia while the main
Binance hosts are blocked by the ISP:
- REST:   https://data-api.binance.vision/api/v3/...  (ticker/24hr, klines)
- Stream: wss://data-stream.binance.vision/stream?streams=<sym>@miniTicker/...
Docs: github.com/binance/binance-spot-api-docs (web-socket-streams.md, faqs/market_data_only.md).
Limits: 1024 streams per connection, 5 incoming messages/s, connections end after 24 h.
Prices are in USDT (a dollar-pegged stablecoin), not exactly USD. Change is over the last 24 hours.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

import websockets

from app.providers.base import CandleData, OnUpdate, Provider, ProviderError, QuoteData, fnum

REST = "https://data-api.binance.vision/api/v3"
WS = "wss://data-stream.binance.vision/stream"
INTERVALS = {"5m": "5m", "1h": "1h", "1d": "1d"}


def _ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def parse_ticker_24hr(d: dict) -> QuoteData:
    price = fnum(d["lastPrice"])
    if price is None:
        raise ProviderError("Binance: no price in response")
    return QuoteData(
        price=price,
        source_timestamp=_ms(d["closeTime"]),
        change=fnum(d.get("priceChange")),
        change_pct=fnum(d.get("priceChangePercent")),
        prev_close=fnum(d.get("openPrice")),
        day_high=fnum(d.get("highPrice")),
        day_low=fnum(d.get("lowPrice")),
        volume=fnum(d.get("volume")),
        quote_currency="USDT",
        change_basis="24h",
    )


def parse_mini_ticker(data: dict) -> tuple[str, QuoteData]:
    """One 24hrMiniTicker event -> (symbol, quote). Change = last price - price 24 h ago."""
    close, open_ = fnum(data["c"]), fnum(data["o"])
    change = close - open_ if close is not None and open_ is not None else None
    q = QuoteData(
        price=close,  # type: ignore[arg-type]
        source_timestamp=_ms(data["E"]),
        change=change,
        change_pct=(change / open_ * 100) if change is not None and open_ else None,
        prev_close=open_,
        day_high=fnum(data.get("h")),
        day_low=fnum(data.get("l")),
        volume=fnum(data.get("v")),
        quote_currency="USDT",
        change_basis="24h",
    )
    return data["s"], q


def parse_klines(rows: list) -> list[CandleData]:
    return [
        CandleData(ts=_ms(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]), volume=float(r[5]))
        for r in rows
    ]


class BinanceProvider(Provider):
    name = "binance"
    label = "Binance"
    kind = "stream"
    poll_interval = 10.0  # REST fallback polling if ever used as a poll source
    expected_delay = 0.0
    candle_ttl = {"5m": 60, "1h": 300, "1d": 3600}

    @property
    def can_stream(self) -> bool:
        return True

    async def get_quote(self, symbol: str) -> QuoteData:
        return parse_ticker_24hr(await self._get_json(f"{REST}/ticker/24hr", params={"symbol": symbol}))

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        rows = await self._get_json(f"{REST}/klines", params={"symbol": symbol, "interval": INTERVALS[timeframe], "limit": min(limit, 1000)})
        return parse_klines(rows)

    async def stream(self, symbols: set[str], on_update: OnUpdate, on_connected: Callable[[], None]) -> None:
        streams = "/".join(f"{s.lower()}@miniTicker" for s in sorted(symbols))
        async with websockets.connect(f"{WS}?streams={streams}", open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            on_connected()
            async for raw in ws:
                msg = json.loads(raw)
                data = msg.get("data") or {}
                if data.get("e") == "24hrMiniTicker":
                    sym, q = parse_mini_ticker(data)
                    on_update(sym, q)

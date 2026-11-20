"""Gate.io public market data (no key) - backup for crypto.

REST:   https://api.gateio.ws/api/v4/spot/tickers, /spot/candlesticks
Stream: wss://api.gateio.ws/ws/v4/  channel "spot.tickers"
Prices are in USDT. Change is over the last 24 hours.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone

import websockets

from app.providers.base import CandleData, OnUpdate, Provider, ProviderError, QuoteData, fnum

REST = "https://api.gateio.ws/api/v4"
WS = "wss://api.gateio.ws/ws/v4/"
INTERVALS = {"5m": "5m", "1h": "1h", "1d": "1d"}


def _ticker_to_quote(t: dict, ts: datetime) -> QuoteData:
    price = fnum(t.get("last"))
    if price is None:
        raise ProviderError("Gate.io: no price in response")
    pct = fnum(t.get("change_percentage"))
    # Gate gives only the 24 h % change; the absolute change follows from it: prev = price / (1 + pct/100).
    prev = price / (1 + pct / 100) if pct is not None and pct != -100 else None
    return QuoteData(
        price=price,
        source_timestamp=ts,
        change=price - prev if prev is not None else None,
        change_pct=pct,
        prev_close=prev,
        day_high=fnum(t.get("high_24h")),
        day_low=fnum(t.get("low_24h")),
        volume=fnum(t.get("base_volume")),
        quote_currency="USDT",
        change_basis="24h",
    )


def parse_rest_tickers(rows: list, received_at: datetime) -> QuoteData:
    """The REST ticker has no timestamp, so the time we received it is used."""
    if not rows:
        raise ProviderError("Gate.io: empty ticker list")
    return _ticker_to_quote(rows[0], received_at)


def parse_ws_ticker(msg: dict) -> tuple[str, QuoteData] | None:
    if msg.get("channel") != "spot.tickers" or msg.get("event") != "update":
        return None
    res = msg["result"]
    ts = datetime.fromtimestamp(msg["time_ms"] / 1000, timezone.utc)
    return res["currency_pair"], _ticker_to_quote(res, ts)


def parse_candles(rows: list) -> list[CandleData]:
    # Row format: [time, quote_volume, close, high, low, open, base_volume, window_closed]
    return [
        CandleData(ts=datetime.fromtimestamp(int(r[0]), timezone.utc), open=float(r[5]), high=float(r[3]),
                   low=float(r[4]), close=float(r[2]), volume=float(r[6]))
        for r in rows
    ]


class GateioProvider(Provider):
    name = "gateio"
    label = "Gate.io"
    kind = "stream"
    poll_interval = 10.0
    candle_ttl = {"5m": 60, "1h": 300, "1d": 3600}

    @property
    def can_stream(self) -> bool:
        return True

    async def get_quote(self, symbol: str) -> QuoteData:
        rows = await self._get_json(f"{REST}/spot/tickers", params={"currency_pair": symbol})
        return parse_rest_tickers(rows, datetime.now(timezone.utc))

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        rows = await self._get_json(f"{REST}/spot/candlesticks",
                                    params={"currency_pair": symbol, "interval": INTERVALS[timeframe], "limit": min(limit, 1000)})
        return parse_candles(rows)

    async def stream(self, symbols: set[str], on_update: OnUpdate, on_connected: Callable[[], None]) -> None:
        async with websockets.connect(WS, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.tickers", "event": "subscribe",
                                      "payload": sorted(symbols)}))
            on_connected()
            async for raw in ws:
                parsed = parse_ws_ticker(json.loads(raw))
                if parsed:
                    on_update(*parsed)

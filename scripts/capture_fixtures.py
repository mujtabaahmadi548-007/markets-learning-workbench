r"""Developer tool: record REAL sample responses from each keyless source into tests/fixtures/.

Tests parse these recorded responses (no network during tests). Re-run to refresh them:
    python scripts\capture_fixtures.py
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import asyncio
import json
from pathlib import Path

import httpx
import websockets

from app.net import USER_AGENT

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)


def save(name: str, data: str | bytes) -> None:
    p = OUT / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    print(f"saved {p.name} ({p.stat().st_size} bytes)")


async def main() -> None:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
        r = await c.get("https://data-api.binance.vision/api/v3/ticker/24hr", params={"symbol": "BTCUSDT"})
        save("binance_ticker_24hr.json", r.text)
        r = await c.get("https://data-api.binance.vision/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1h", "limit": 5})
        save("binance_klines_1h.json", r.text)
        r = await c.get("https://api.gateio.ws/api/v4/spot/tickers", params={"currency_pair": "BTC_USDT"})
        save("gateio_ticker.json", r.text)
        r = await c.get("https://api.gateio.ws/api/v4/spot/candlesticks", params={"currency_pair": "BTC_USDT", "interval": "1h", "limit": 5})
        save("gateio_candles_1h.json", r.text)
        r = await c.get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": "bitcoin,ethereum", "vs_currencies": "usd", "include_24hr_change": "true",
            "include_24hr_vol": "true", "include_last_updated_at": "true"})
        save("coingecko_simple_price.json", r.text)
        r = await c.get("https://api.gold-api.com/price/XAU")
        save("goldapi_xau.json", r.text)
        for sym, name in (("CL=F", "yahoo_chart_clf_5m.json"), ("AAPL", "yahoo_chart_aapl_1d.json")):
            params = {"interval": "5m", "range": "1d"} if sym == "CL=F" else {"interval": "1d", "range": "5d"}
            r = await c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params=params)
            save(name, r.text)
        r = await c.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DCOILWTICO", "cosd": "2026-10-01"})
        save("fred_dcoilwtico.csv", r.text)
        r = await c.get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all",
                        params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": "2026", "page": "", "_format": "csv"})
        save("treasury_2026.csv", "\n".join(r.text.splitlines()[:15]))
        r = await c.get("https://api.twelvedata.com/quote", params={"symbol": "AAPL", "apikey": "demo"})
        save("twelvedata_quote_aapl_demo.json", r.text)
        r = await c.get("https://api.twelvedata.com/time_series", params={"symbol": "AAPL", "interval": "1day", "outputsize": 3, "apikey": "demo"})
        save("twelvedata_timeseries_aapl_demo.json", r.text)

    async with websockets.connect("wss://data-stream.binance.vision/stream?streams=btcusdt@miniTicker/ethusdt@miniTicker") as ws:
        msgs = [json.loads(await ws.recv()) for _ in range(3)]
    save("binance_ws_miniticker.json", json.dumps(msgs, indent=1))

    async with websockets.connect("wss://api.gateio.ws/ws/v4/") as ws:
        import time

        await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.tickers", "event": "subscribe", "payload": ["BTC_USDT"]}))
        msgs = []
        while len(msgs) < 2:
            m = json.loads(await ws.recv())
            msgs.append(m)
    save("gateio_ws_tickers.json", json.dumps(msgs, indent=1))


if __name__ == "__main__":
    asyncio.run(main())

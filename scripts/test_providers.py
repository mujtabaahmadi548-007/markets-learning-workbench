r"""Check every data source once and print an OK / FAIL table.

Run from the project folder:
    .\.venv\Scripts\python.exe scripts\test_providers.py
    .\.venv\Scripts\python.exe scripts\test_providers.py --quick     # skip the 20-second Finnhub stream listen

Keys come from .env and are never printed. Sources whose key is missing show SKIP.
Prices printed here are real values returned by each source, with the source's own timestamp.
Note: Finnhub allows ONE stream connection per key. From Stage 1 on, stop the app before running
this script, or the Finnhub stream check will fail.
Uses: 1 Alpha Vantage request (of 25/day), about 5 Twelve Data credits (of 800/day).
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (path + TLS setup)

import argparse
import asyncio
import csv
import io
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import websockets

from app.config import load_settings
from app.logging_setup import redact
from app.net import USER_AGENT
from app.timeutil import fmt_jakarta

S = load_settings()


@dataclass
class Result:
    group: str
    check: str
    status: str  # OK | FAIL | SKIP | WARN
    detail: str


results: list[Result] = []


def add(group: str, check: str, status: str, detail: str) -> None:
    results.append(Result(group, check, status, redact(detail)[:150]))


def ts_ms(ms: float) -> str:
    return fmt_jakarta(datetime.fromtimestamp(ms / 1000, timezone.utc))


def ts_s(sec: float) -> str:
    return fmt_jakarta(datetime.fromtimestamp(sec, timezone.utc))


async def get(c: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    return await c.get(url, **kw)


# ----------------------------------------------------------------------------- crypto
async def check_binance(c: httpx.AsyncClient) -> None:
    g = "Crypto"
    try:
        r = await get(c, "https://data-api.binance.vision/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1d", "limit": 2})
        r.raise_for_status()
        k = r.json()[-1]
        add(g, "Binance candles (REST)", "OK", f"BTCUSDT 1d candle open {ts_ms(k[0])}, close so far {float(k[4]):,.2f} USDT")
    except Exception as e:  # noqa: BLE001
        add(g, "Binance candles (REST)", "FAIL", f"{type(e).__name__}: {e}")
    try:
        async with websockets.connect("wss://data-stream.binance.vision/ws/btcusdt@trade", open_timeout=10) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        add(g, "Binance live stream", "OK", f"BTCUSDT trade {float(msg['p']):,.2f} USDT at {ts_ms(msg['T'])}")
    except Exception as e:  # noqa: BLE001
        add(g, "Binance live stream", "FAIL", f"{type(e).__name__}: {e}")


async def check_gateio() -> None:
    g = "Crypto"
    try:
        async with websockets.connect("wss://api.gateio.ws/ws/v4/", open_timeout=10) as ws:
            await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.tickers", "event": "subscribe", "payload": ["BTC_USDT"]}))
            deadline = time.time() + 12
            while time.time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), deadline - time.time()))
                if msg.get("event") == "update":
                    res = msg["result"]
                    add(g, "Gate.io live stream (backup)", "OK", f"BTC_USDT {float(res['last']):,.2f} USDT at {ts_ms(msg['time_ms'])}")
                    return
        add(g, "Gate.io live stream (backup)", "FAIL", "no update within 12 s")
    except Exception as e:  # noqa: BLE001
        add(g, "Gate.io live stream (backup)", "FAIL", f"{type(e).__name__}: {e}")


async def check_coingecko(c: httpx.AsyncClient) -> None:
    g = "Crypto"
    headers = {"x-cg-demo-api-key": S.coingecko_api_key} if S.coingecko_api_key else {}
    label = "CoinGecko (backup, demo key)" if S.coingecko_api_key else "CoinGecko (backup, no key)"
    try:
        r = await get(c, "https://api.coingecko.com/api/v3/simple/price",
                      params={"ids": "bitcoin", "vs_currencies": "usd", "include_last_updated_at": "true"}, headers=headers)
        r.raise_for_status()
        b = r.json()["bitcoin"]
        add(g, label, "OK", f"BTC {b['usd']:,} USD, updated {ts_s(b['last_updated_at'])}")
    except Exception as e:  # noqa: BLE001
        add(g, label, "FAIL", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------- metals
async def check_goldapi(c: httpx.AsyncClient) -> None:
    g = "Metals"
    for sym in ("XAU", "XAG"):
        try:
            r = await get(c, f"https://api.gold-api.com/price/{sym}")
            r.raise_for_status()
            d = r.json()
            add(g, f"gold-api.com {sym}", "OK", f"{d['name']} {d['price']:,.2f} USD, updated {fmt_jakarta(datetime.fromisoformat(d['updatedAt'].replace('Z', '+00:00')))}")
        except Exception as e:  # noqa: BLE001
            add(g, f"gold-api.com {sym}", "FAIL", f"{type(e).__name__}: {e}")


async def check_alphavantage(c: httpx.AsyncClient) -> None:
    g = "Metals"
    if not S.alpha_vantage_api_key:
        add(g, "Alpha Vantage gold spot", "SKIP", "no ALPHA_VANTAGE_API_KEY in .env (optional)")
        return
    try:
        r = await get(c, "https://www.alphavantage.co/query", params={"function": "GOLD_SILVER_SPOT", "symbol": "XAU", "apikey": S.alpha_vantage_api_key})
        d = r.json()
        if any(k in d for k in ("Information", "Note", "Error Message")):
            add(g, "Alpha Vantage gold spot", "FAIL", str(d)[:150])
        else:
            add(g, "Alpha Vantage gold spot", "OK", json.dumps(d)[:150])
    except Exception as e:  # noqa: BLE001
        add(g, "Alpha Vantage gold spot", "FAIL", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------- stocks (Finnhub, Twelve Data)
async def check_finnhub_rest(c: httpx.AsyncClient) -> None:
    g = "Stocks"
    if not S.finnhub_api_key:
        add(g, "Finnhub quote / news / OANDA list", "SKIP", "no FINNHUB_API_KEY in .env")
        return
    p = {"token": S.finnhub_api_key}
    try:
        r = await get(c, "https://finnhub.io/api/v1/quote", params={"symbol": "AAPL", **p})
        d = r.json()
        if r.status_code == 200 and d.get("c"):
            add(g, "Finnhub quote", "OK", f"AAPL {d['c']:,.2f} USD ({d['dp']:+.2f}% vs prev close {d['pc']:,.2f}), quote time {ts_s(d['t'])}")
        else:
            add(g, "Finnhub quote", "FAIL", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        add(g, "Finnhub quote", "FAIL", f"{type(e).__name__}: {e}")
    try:
        today = datetime.now(timezone.utc).date()
        r = await get(c, "https://finnhub.io/api/v1/company-news", params={"symbol": "NVDA", "from": str(today - timedelta(days=2)), "to": str(today), **p})
        items = r.json() if r.status_code == 200 else []
        if isinstance(items, list) and items:
            add(g, "Finnhub company news", "OK", f"{len(items)} NVDA articles in 2 days; newest: {items[0].get('headline', '')[:70]}")
        else:
            add(g, "Finnhub company news", "FAIL", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        add(g, "Finnhub company news", "FAIL", f"{type(e).__name__}: {e}")
    try:
        r = await get(c, "https://finnhub.io/api/v1/forex/symbol", params={"exchange": "oanda", **p})
        syms = {x.get("symbol") for x in r.json()} if r.status_code == 200 and isinstance(r.json(), list) else set()
        wanted = ["OANDA:XAU_USD", "OANDA:XAG_USD", "OANDA:WTICO_USD", "OANDA:BCO_USD", "OANDA:NATGAS_USD"]
        if syms:
            found = [w for w in wanted if w in syms]
            add(g, "Finnhub OANDA symbol list", "OK" if found else "WARN", f"available: {', '.join(found) or 'none of gold/silver/oil/gas'}")
        else:
            add(g, "Finnhub OANDA symbol list", "FAIL", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        add(g, "Finnhub OANDA symbol list", "FAIL", f"{type(e).__name__}: {e}")


async def check_finnhub_stream(seconds: int) -> None:
    g = "Stocks"
    if not S.finnhub_api_key:
        add(g, "Finnhub live stream", "SKIP", "no FINNHUB_API_KEY in .env")
        return
    symbols = ["AAPL", "NVDA", "SPY", "BINANCE:BTCUSDT", "OANDA:XAU_USD", "OANDA:XAG_USD", "OANDA:WTICO_USD", "OANDA:BCO_USD", "OANDA:NATGAS_USD"]
    counts: Counter[str] = Counter()
    last: dict[str, tuple[float, int]] = {}
    try:
        async with websockets.connect(f"wss://ws.finnhub.io?token={S.finnhub_api_key}", open_timeout=10) as ws:
            for s in symbols:
                await ws.send(json.dumps({"type": "subscribe", "symbol": s}))
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, deadline - time.time())))
                except asyncio.TimeoutError:
                    break
                if msg.get("type") == "trade":
                    for t in msg.get("data", []):
                        counts[t["s"]] += 1
                        last[t["s"]] = (t["p"], t["t"])
                elif msg.get("type") == "error":
                    add(g, "Finnhub live stream", "FAIL", str(msg.get("msg"))[:150])
                    return
    except Exception as e:  # noqa: BLE001
        add(g, "Finnhub live stream", "FAIL", f"{type(e).__name__}: {e}")
        return
    for s in symbols:
        if counts[s]:
            p, t = last[s]
            add(g, f"Finnhub stream {s}", "OK", f"{counts[s]} updates in {seconds}s, last {p:,.4f} at {ts_ms(t)}")
        else:
            add(g, f"Finnhub stream {s}", "WARN", f"no updates in {seconds}s (normal if that market is closed now, or not on the free plan)")


async def check_twelvedata(c: httpx.AsyncClient) -> None:
    g = "Stocks"
    if not S.twelve_data_api_key:
        add(g, "Twelve Data", "SKIP", "no TWELVE_DATA_API_KEY in .env")
        return
    p = {"apikey": S.twelve_data_api_key}
    checks = [
        ("Twelve Data quote AAPL", "https://api.twelvedata.com/quote", {"symbol": "AAPL"}),
        ("Twelve Data candles AAPL", "https://api.twelvedata.com/time_series", {"symbol": "AAPL", "interval": "1day", "outputsize": 2}),
        ("Twelve Data price XAU/USD", "https://api.twelvedata.com/price", {"symbol": "XAU/USD"}),
        ("Twelve Data price WTI/USD", "https://api.twelvedata.com/price", {"symbol": "WTI/USD"}),
    ]
    for name, url, params in checks:
        try:
            r = await get(c, url, params={**params, **p})
            d = r.json()
            if d.get("status") == "error" or d.get("code"):
                add(g, name, "FAIL", f"{d.get('code')}: {d.get('message', '')[:130]}")
            elif "close" in d and "symbol" in d:
                add(g, name, "OK", f"{d['symbol']} close {float(d['close']):,.2f} on {d.get('datetime')} ({d.get('exchange', '')})")
            elif "values" in d:
                v = d["values"][0]
                add(g, name, "OK", f"latest daily candle {v['datetime']}: close {float(v['close']):,.2f}")
            elif "price" in d:
                add(g, name, "OK", f"price {float(d['price']):,.2f}")
            else:
                add(g, name, "WARN", json.dumps(d)[:150])
        except Exception as e:  # noqa: BLE001
            add(g, name, "FAIL", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------- macro / oil / calendar
async def check_official(c: httpx.AsyncClient) -> None:
    year = datetime.now(timezone.utc).year
    try:
        r = await get(c, f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all",
                      params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "page": "", "_format": "csv"})
        rows = list(csv.DictReader(io.StringIO(r.text)))
        add("Macro", "US Treasury 10-year yield", "OK", f"{rows[0]['10 Yr']}% on {rows[0]['Date']} (official, end of day)")
    except Exception as e:  # noqa: BLE001
        add("Macro", "US Treasury 10-year yield", "FAIL", f"{type(e).__name__}: {e}")
    for series, label in (("DCOILWTICO", "WTI"), ("DCOILBRENTEU", "Brent")):
        try:
            r = await get(c, "https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series})
            rows = [row for row in csv.reader(io.StringIO(r.text))][1:]
            rows = [row for row in rows if row[1] not in ("", ".")]
            add("Energy", f"EIA {label} spot via FRED (daily)", "OK", f"{label} {float(rows[-1][1]):,.2f} USD/bbl on {rows[-1][0]} (official, ~1 business day behind)")
        except Exception as e:  # noqa: BLE001
            add("Energy", f"EIA {label} spot via FRED (daily)", "FAIL", f"{type(e).__name__}: {e}")
    try:
        r = await get(c, "https://www.bls.gov/schedule/news_release/bls.ics")
        n_cpi = r.text.count("SUMMARY:Consumer Price Index")
        n_jobs = r.text.count("SUMMARY:Employment Situation")
        add("Calendar", "BLS release calendar", "OK" if r.status_code == 200 and n_cpi else "FAIL", f"HTTP {r.status_code}; {n_cpi} CPI and {n_jobs} jobs-report dates")
    except Exception as e:  # noqa: BLE001
        add("Calendar", "BLS release calendar", "FAIL", f"{type(e).__name__}: {e}")
    try:
        r = await get(c, "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
        ok = r.status_code == 200 and f"{year} FOMC Meetings" in r.text
        add("Calendar", "Fed FOMC meeting dates", "OK" if ok else "FAIL", f"HTTP {r.status_code}; {year} meetings section {'found' if ok else 'not found'}")
    except Exception as e:  # noqa: BLE001
        add("Calendar", "Fed FOMC meeting dates", "FAIL", f"{type(e).__name__}: {e}")


async def check_unofficial(c: httpx.AsyncClient) -> None:
    if not S.allow_unofficial_sources:
        add("Unofficial", "Yahoo / ForexFactory", "SKIP", "ALLOW_UNOFFICIAL_SOURCES=false")
        return
    now = time.time()
    for sym, label in (("CL=F", "WTI futures"), ("BZ=F", "Brent futures"), ("DX-Y.NYB", "US Dollar Index"), ("^TNX", "10Y yield x10")):
        try:
            r = await get(c, f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"interval": "1d", "range": "5d"})
            meta = r.json()["chart"]["result"][0]["meta"]
            age_min = (now - meta["regularMarketTime"]) / 60
            add("Unofficial", f"Yahoo {label} ({sym})", "OK", f"{meta['regularMarketPrice']:,.3f}, market time {ts_s(meta['regularMarketTime'])} ({age_min:.0f} min old)")
        except Exception as e:  # noqa: BLE001
            add("Unofficial", f"Yahoo {label} ({sym})", "FAIL", f"{type(e).__name__}: {e}")
    try:
        r = await get(c, "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
        events = r.json()
        high = [e for e in events if e.get("country") == "USD" and e.get("impact") == "High"]
        add("Unofficial", "ForexFactory weekly calendar", "OK", f"{len(events)} events this week, {len(high)} high-impact USD")
    except Exception as e:  # noqa: BLE001
        add("Unofficial", "ForexFactory weekly calendar", "FAIL", f"{type(e).__name__}: {e}")


async def check_gemini_reachable(c: httpx.AsyncClient) -> None:
    """Only checks that Google's API host is reachable; no key is sent. Use scripts/test_gemini.py for the real test."""
    try:
        r = await get(c, "https://generativelanguage.googleapis.com/v1beta/models")
        add("AI", "Gemini API host reachable", "OK" if r.status_code in (200, 400, 401, 403) else "FAIL",
            f"HTTP {r.status_code} without key (expected); run scripts\\test_gemini.py for the real test")
    except Exception as e:  # noqa: BLE001
        add("AI", "Gemini API host reachable", "FAIL", f"{type(e).__name__}: {e}")


async def main_async(quick: bool) -> None:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        await asyncio.gather(
            check_binance(c), check_gateio(), check_coingecko(c),
            check_goldapi(c), check_alphavantage(c),
            check_finnhub_rest(c), check_twelvedata(c),
            check_official(c), check_unofficial(c), check_gemini_reachable(c),
            check_finnhub_stream(5 if quick else 20),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="listen to the Finnhub stream for 5 s instead of 20 s")
    args = parser.parse_args()
    print(f"\nChecking data sources at {fmt_jakarta(datetime.now(timezone.utc))} …\n")
    asyncio.run(main_async(args.quick))

    order = ["Crypto", "Stocks", "Metals", "Energy", "Macro", "Calendar", "Unofficial", "AI"]
    results.sort(key=lambda r: (order.index(r.group) if r.group in order else 99, r.check))
    print(f"{'GROUP':10} {'CHECK':36} {'RESULT':6}  DETAIL")
    print("-" * 120)
    for r in results:
        print(f"{r.group:10} {r.check:36} {r.status:6}  {r.detail}")
    tally = Counter(r.status for r in results)
    print("-" * 120)
    print(f"OK: {tally['OK']}   WARN: {tally['WARN']}   FAIL: {tally['FAIL']}   SKIP: {tally['SKIP']}")
    print("WARN = reachable but no data right now (often: market closed). SKIP = key not set in .env.\n")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())

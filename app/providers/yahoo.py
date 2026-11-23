"""Yahoo Finance chart endpoint - UNOFFICIAL, last-resort fallback only.

https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=..&range=..
Undocumented; Yahoo's terms restrict automated use, so it is for personal use only and can be
switched off with ALLOW_UNOFFICIAL_SOURCES=false. Every value from it is labelled "Unofficial".
Measured on 20 Nov 2026: futures (CL=F, BZ=F, GC=F, DX-Y.NYB) about 10 minutes behind, ^TNX 15-30 minutes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.providers.base import CandleData, Provider, ProviderError, ProviderRateLimited, QuoteData, RateLimiter, fnum

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HISTORY_START = -2208988800  # 1 Jan 1900, before any listed history
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
# timeframe -> (interval, range)
CANDLE_PARAMS = {"5m": ("5m", "5d"), "1h": ("60m", "3mo"), "1d": ("1d", "2y")}


def _result(d: dict) -> dict:
    chart = d.get("chart") or {}
    if chart.get("error"):
        raise ProviderError(f"Yahoo: {chart['error'].get('description') or chart['error'].get('code')}")
    res = chart.get("result") or []
    if not res:
        raise ProviderError("Yahoo: empty response")
    return res[0]


def parse_quote(d: dict) -> QuoteData:
    """Expects a response requested with range=1d, where previousClose is the prior session's close."""
    m = _result(d)["meta"]
    price = fnum(m.get("regularMarketPrice"))
    ts = m.get("regularMarketTime")
    if price is None or not ts:
        raise ProviderError("Yahoo: missing price or time")
    prev = fnum(m.get("previousClose")) or fnum(m.get("chartPreviousClose"))
    change = price - prev if prev else None
    return QuoteData(
        price=price,
        source_timestamp=datetime.fromtimestamp(int(ts), timezone.utc),
        change=change,
        change_pct=(change / prev * 100) if change is not None and prev else None,
        prev_close=prev,
        day_high=fnum(m.get("regularMarketDayHigh")),
        day_low=fnum(m.get("regularMarketDayLow")),
        volume=fnum(m.get("regularMarketVolume")),
        quote_currency=m.get("currency") or "USD",
    )


def parse_candles(d: dict) -> list[CandleData]:
    r = _result(d)
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, t in enumerate(ts):
        o, h, low, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, low, c):
            continue  # Yahoo leaves gaps as null; skip them instead of inventing values
        v = (q.get("volume") or [None] * len(ts))[i]
        # EPOCH + timedelta, not fromtimestamp: Windows rejects negative (pre-1970) timestamps, e.g. the S&P 500 in 1927.
        out.append(CandleData(ts=EPOCH + timedelta(seconds=int(t)), open=float(o), high=float(h),
                              low=float(low), close=float(c), volume=float(v) if v is not None else None))
    return out


class YahooProvider(Provider):
    name = "yahoo"
    label = "Yahoo Finance (unofficial)"
    kind = "poll"
    poll_interval = 60.0
    expected_delay = 1800.0  # up to ~30 min for some symbols
    unofficial = True
    candle_ttl = {"5m": 300, "1h": 1800, "1d": 6 * 3600}

    def __init__(self, http, api_key: str = "") -> None:  # noqa: ANN001
        super().__init__(http, api_key)
        self._limiter = RateLimiter(30)

    has_history = True  # full daily history (see get_history)

    async def _chart(self, symbol: str, params: dict, timeout: float = 15.0) -> dict:
        """Yahoo answers non-browser clients (and busy shared IPs such as GitHub's runners) with HTTP 429 more
        often, so ask like a browser and retry a rate limit twice after a short wait."""
        for attempt, wait in enumerate((0.0, 2.0, 6.0)):
            if wait:
                await asyncio.sleep(wait)
            await self._limiter.acquire()
            try:
                return await self._get_json(URL.format(symbol=symbol), params=params, headers=BROWSER_HEADERS, timeout=timeout)
            except ProviderRateLimited:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    async def get_quote(self, symbol: str) -> QuoteData:
        return parse_quote(await self._chart(symbol, {"interval": "1m", "range": "1d"}))

    async def get_history(self, symbol: str) -> list[CandleData]:
        """Every daily candle since the first trade (e.g. AAPL from 12 Dec 1980, the S&P 500 from 1927).
        range=max would silently switch to monthly or quarterly bars and cut the start, so an explicit period
        from 1900 until now is asked for instead (measured 24 Nov 2026: true daily bars, 2-7 s, up to 2.6 MB).
        Prices are split-adjusted by Yahoo, not dividend-adjusted (price return)."""
        now = int(datetime.now(timezone.utc).timestamp())
        d = await self._chart(symbol, {"interval": "1d", "period1": HISTORY_START, "period2": now}, timeout=60.0)
        return parse_candles(d)

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        interval, rng = CANDLE_PARAMS[timeframe]
        candles = parse_candles(await self._chart(symbol, {"interval": interval, "range": rng}))
        return candles[-limit:]

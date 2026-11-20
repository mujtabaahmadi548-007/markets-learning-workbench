"""Yahoo Finance chart endpoint - UNOFFICIAL, last-resort fallback only.

https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=..&range=..
Undocumented; Yahoo's terms restrict automated use, so it is for personal use only and can be
switched off with ALLOW_UNOFFICIAL_SOURCES=false. Every value from it is labelled "Unofficial".
Measured on 20 Nov 2026: futures (CL=F, BZ=F, GC=F, DX-Y.NYB) about 10 minutes behind, ^TNX 15-30 minutes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.providers.base import CandleData, Provider, ProviderError, QuoteData, RateLimiter, fnum

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
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
        out.append(CandleData(ts=datetime.fromtimestamp(int(t), timezone.utc), open=float(o), high=float(h),
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

    async def get_quote(self, symbol: str) -> QuoteData:
        await self._limiter.acquire()
        return parse_quote(await self._get_json(URL.format(symbol=symbol), params={"interval": "5m", "range": "1d"}))

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        interval, rng = CANDLE_PARAMS[timeframe]
        await self._limiter.acquire()
        candles = parse_candles(await self._get_json(URL.format(symbol=symbol), params={"interval": interval, "range": rng}))
        return candles[-limit:]

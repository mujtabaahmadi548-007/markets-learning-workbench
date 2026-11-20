"""Twelve Data (free key: TWELVE_DATA_API_KEY) - stock quotes (backup) and stock candles.

https://api.twelvedata.com/quote, /time_series   (1 credit per symbol per call)
Free (Basic) plan: 8 credits/minute, 800/day (resets 00:00 UTC). US stocks, forex and crypto;
commodities (gold, oil) are NOT included on the free plan.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.providers.base import CandleData, DailyBudget, Provider, ProviderError, QuoteData, RateLimiter, fnum

REST = "https://api.twelvedata.com"
INTERVALS = {"5m": "5min", "1h": "1h", "1d": "1day"}


def _check(d: dict) -> None:
    if isinstance(d, dict) and (d.get("status") == "error" or ("code" in d and "message" in d)):
        raise ProviderError(f"Twelve Data: {str(d.get('message'))[:150]}")


def parse_quote(d: dict) -> QuoteData:
    _check(d)
    price = fnum(d.get("close"))
    ts = d.get("last_quote_at") or d.get("timestamp")
    if price is None or not ts:
        raise ProviderError("Twelve Data: missing price or time")
    return QuoteData(
        price=price,
        source_timestamp=datetime.fromtimestamp(int(ts), timezone.utc),
        change=fnum(d.get("change")),
        change_pct=fnum(d.get("percent_change")),
        prev_close=fnum(d.get("previous_close")),
        day_high=fnum(d.get("high")),
        day_low=fnum(d.get("low")),
        volume=fnum(d.get("volume")),
        quote_currency=d.get("currency") or "USD",
    )


def parse_time_series(d: dict) -> list[CandleData]:
    """Requested with timezone=UTC, so datetimes are UTC ('YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS')."""
    _check(d)
    out = []
    for v in d.get("values") or []:
        s = v["datetime"]
        fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
        ts = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        out.append(CandleData(ts=ts, open=float(v["open"]), high=float(v["high"]), low=float(v["low"]),
                              close=float(v["close"]), volume=fnum(v.get("volume"))))
    out.sort(key=lambda c: c.ts)
    return out


class TwelveDataProvider(Provider):
    name = "twelvedata"
    label = "Twelve Data"
    kind = "poll"
    poll_interval = 120.0  # backup quotes only; keeps credits for candles
    expected_delay = 60.0
    requires_key = True
    # Candles are refreshed slowly to stay inside 800 credits/day.
    candle_ttl = {"5m": 900, "1h": 3600, "1d": 6 * 3600}

    def __init__(self, http, api_key: str = "") -> None:  # noqa: ANN001
        super().__init__(http, api_key)
        self._limiter = RateLimiter(8)
        self.budget = DailyBudget(780)  # keep a small reserve below 800

    async def _call(self, path: str, params: dict) -> dict:
        if not self.configured:
            raise ProviderError("Twelve Data: no TWELVE_DATA_API_KEY in .env")
        self.budget.take()
        await self._limiter.acquire()
        return await self._get_json(f"{REST}/{path}", params={**params, "apikey": self.api_key})

    async def get_quote(self, symbol: str) -> QuoteData:
        return parse_quote(await self._call("quote", {"symbol": symbol}))

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        d = await self._call("time_series", {"symbol": symbol, "interval": INTERVALS[timeframe],
                                             "outputsize": min(limit, 5000), "timezone": "UTC"})
        return parse_time_series(d)

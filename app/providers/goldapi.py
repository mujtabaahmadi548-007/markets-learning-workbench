"""gold-api.com (no key) - spot gold (XAU) and silver (XAG) in USD.

https://api.gold-api.com/price/{XAU|XAG}  -> {"price", "updatedAt", ...}
The site says real-time prices have no rate limit, but sending several requests per second can
get an IP banned, so this app asks every 15 s at most. The response has no previous close, so the
day's change is computed by the app from its own recorded prices (24 h earlier) when available.
History/OHLC endpoints need a key (x-api-key) and are not used.
"""
from __future__ import annotations

from datetime import datetime

from app.providers.base import Provider, ProviderError, QuoteData, RateLimiter, fnum


def parse_price(d: dict) -> QuoteData:
    price = fnum(d.get("price"))
    if price is None or not d.get("updatedAt"):
        raise ProviderError("gold-api.com: missing price or time")
    return QuoteData(price=price, source_timestamp=datetime.fromisoformat(d["updatedAt"].replace("Z", "+00:00")),
                     quote_currency=d.get("currency") or "USD")


class GoldApiProvider(Provider):
    name = "goldapi"
    label = "gold-api.com"
    kind = "poll"
    poll_interval = 15.0
    expected_delay = 30.0

    def __init__(self, http, api_key: str = "") -> None:  # noqa: ANN001
        super().__init__(http, api_key)
        self._limiter = RateLimiter(20)

    async def get_quote(self, symbol: str) -> QuoteData:
        await self._limiter.acquire()
        return parse_price(await self._get_json(f"https://api.gold-api.com/price/{symbol}"))

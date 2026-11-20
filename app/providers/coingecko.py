"""CoinGecko (no key, or a free Demo key) - last crypto backup.

https://api.coingecko.com/api/v3/simple/price  (header x-cg-demo-api-key when a Demo key is set)
Prices are in USD, cached by CoinGecko for about 1-5 minutes. Change is over 24 hours.
One request fetches all coins at once and is reused for 30 s, to stay well inside the free limits.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app.providers.base import Provider, ProviderError, QuoteData, RateLimiter, fnum

URL = "https://api.coingecko.com/api/v3/simple/price"


def parse_simple_price(d: dict, coin_id: str) -> QuoteData:
    row = d.get(coin_id)
    if not row or row.get("usd") is None:
        raise ProviderError(f"CoinGecko: no price for {coin_id}")
    price = float(row["usd"])
    pct = fnum(row.get("usd_24h_change"))
    prev = price / (1 + pct / 100) if pct is not None and pct != -100 else None
    return QuoteData(
        price=price,
        source_timestamp=datetime.fromtimestamp(int(row["last_updated_at"]), timezone.utc),
        change=price - prev if prev is not None else None,
        change_pct=pct,
        prev_close=prev,
        volume=None,  # CoinGecko's 24h volume is in USD across all exchanges; not comparable, so not shown
        quote_currency="USD",
        change_basis="24h",
    )


class CoinGeckoProvider(Provider):
    name = "coingecko"
    label = "CoinGecko"
    kind = "poll"
    poll_interval = 60.0
    expected_delay = 300.0

    def __init__(self, http, api_key: str = "", coin_ids: set[str] | None = None) -> None:  # noqa: ANN001
        super().__init__(http, api_key)
        self.coin_ids: set[str] = set(coin_ids or ())
        self._cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()
        self._limiter = RateLimiter(10)

    async def _batch(self) -> dict:
        async with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < 30:
                return self._cache[1]
            await self._limiter.acquire()
            headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else {}
            data = await self._get_json(URL, params={"ids": ",".join(sorted(self.coin_ids)), "vs_currencies": "usd",
                                                     "include_24hr_change": "true", "include_last_updated_at": "true"},
                                        headers=headers)
            self._cache = (time.monotonic(), data)
            return data

    async def get_quote(self, symbol: str) -> QuoteData:
        self.coin_ids.add(symbol)
        return parse_simple_price(await self._batch(), symbol)

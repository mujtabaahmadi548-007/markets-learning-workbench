"""FRED (St. Louis Fed) daily series via the public CSV download (no key).

https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES&cosd=YYYY-MM-DD
Official data, one value per business day, usually published the next business day.
Series used: DCOILWTICO (WTI spot, from EIA), DCOILBRENTEU (Brent spot, EIA), DHHNGSP (Henry Hub gas, EIA),
SP500, NASDAQCOM, DTWEXBGS (Fed Broad Dollar Index - NOT the same as DXY).
Only the date is known for each value, so the time is shown as a date.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from app.providers.base import CandleData, Provider, ProviderError, QuoteData, fnum
from app.services.status import daily_stamp

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_csv(text: str) -> list[tuple[date, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise ProviderError("FRED: unexpected CSV")
    out = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        v = fnum(r[1])
        if v is None:
            continue  # FRED marks missing days (holidays) with "." or empty
        out.append((date.fromisoformat(r[0]), v))
    if not out:
        raise ProviderError("FRED: no values")
    return out


def quote_from_series(values: list[tuple[date, float]]) -> QuoteData:
    (d_last, last) = values[-1]
    prev = values[-2][1] if len(values) > 1 else None
    change = last - prev if prev is not None else None
    return QuoteData(price=last, source_timestamp=daily_stamp(d_last), change=change,
                     change_pct=(change / prev * 100) if change is not None and prev else None,
                     prev_close=prev, quote_currency="USD", change_basis="prev_obs")


def candles_from_series(values: list[tuple[date, float]]) -> list[CandleData]:
    """Close-only data: open = high = low = close. Charts draw these as a line, not candles."""
    return [CandleData(ts=daily_stamp(d), open=v, high=v, low=v, close=v) for d, v in values]


class FredProvider(Provider):
    name = "fred"
    label = "FRED (St. Louis Fed)"
    kind = "daily"
    poll_interval = 3600.0
    close_only = True
    candle_ttl = {"1d": 3 * 3600}

    async def _series(self, series: str, days: int) -> list[tuple[date, float]]:
        start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        return parse_csv(await self._get_text(URL, params={"id": series, "cosd": start}))

    async def get_quote(self, symbol: str) -> QuoteData:
        return quote_from_series(await self._series(symbol, 20))

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        if timeframe != "1d":
            raise ProviderError("FRED has daily values only")
        return candles_from_series(await self._series(symbol, 800))[-limit:]

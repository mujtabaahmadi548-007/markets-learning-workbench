"""US Treasury daily par yield curve (official, no key).

https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all
    ?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv
One row per business day (newest first), published after the market close. Symbol = column name, e.g. "10 Yr".
Yields are in percent; their change is shown in percentage points (pp), not as a % of a %.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

from app.providers.base import CandleData, Provider, ProviderError, QuoteData, fnum
from app.services.status import daily_stamp

URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"


def parse_csv(text: str, column: str) -> list[tuple[date, float]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or column not in reader.fieldnames:
        raise ProviderError(f"US Treasury: column {column!r} not found")
    out = []
    for row in reader:
        v = fnum(row.get(column))
        if v is None:
            continue
        out.append((datetime.strptime(row["Date"], "%m/%d/%Y").date(), v))
    out.sort()
    return out


class TreasuryProvider(Provider):
    name = "treasury"
    label = "US Treasury"
    kind = "daily"
    poll_interval = 3600.0
    close_only = True
    candle_ttl = {"1d": 3 * 3600}

    async def _year(self, year: int, column: str) -> list[tuple[date, float]]:
        text = await self._get_text(URL.format(year=year), params={"type": "daily_treasury_yield_curve",
                                                                  "field_tdr_date_value": year, "page": "", "_format": "csv"})
        return parse_csv(text, column)

    async def get_quote(self, symbol: str) -> QuoteData:
        year = datetime.now(timezone.utc).year
        values = await self._year(year, symbol)
        if len(values) < 2:  # early January: include last year
            values = await self._year(year - 1, symbol) + values
        if not values:
            raise ProviderError("US Treasury: no values")
        d_last, last = values[-1]
        prev = values[-2][1] if len(values) > 1 else None
        return QuoteData(price=last, source_timestamp=daily_stamp(d_last),
                         change=(last - prev) if prev is not None else None, change_pct=None,
                         prev_close=prev, quote_currency="%", change_basis="prev_obs")

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        if timeframe != "1d":
            raise ProviderError("US Treasury has daily values only")
        year = datetime.now(timezone.utc).year
        values = await self._year(year - 1, symbol) + await self._year(year, symbol)
        return [CandleData(ts=daily_stamp(d), open=v, high=v, low=v, close=v) for d, v in values][-limit:]

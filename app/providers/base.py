"""Provider interface shared by every data source.

Each source is one class (one file) implementing some of:
    get_quote(symbol)                  -> QuoteData       latest price (+ change, range, volume if the source gives them)
    get_candles(symbol, timeframe, n)  -> list[CandleData] history for charts (timeframes: 5m, 1h, 1d)
    stream(symbols, on_update, stop)   -> runs a live WebSocket feed, calling on_update(symbol, QuoteData)
    get_news(...)                      -> added in Stage 2
Unsupported operations raise UnsupportedError.

Every number in QuoteData/CandleData comes straight from the provider's response, or is simple
arithmetic on those values (e.g. change = price - previous close). Nothing is estimated.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.net import USER_AGENT

TIMEFRAMES = ("5m", "1h", "1d")


class ProviderError(Exception):
    """A provider call failed. The message is safe to show (no keys)."""


class ProviderNotConfigured(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class UnsupportedError(ProviderError):
    pass


@dataclass
class QuoteData:
    price: float
    source_timestamp: datetime
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    quote_currency: str = "USD"
    # What "change" is measured against: prev_close | 24h | prev_obs (previous daily value)
    change_basis: str = "prev_close"


@dataclass
class CandleData:
    ts: datetime  # candle open time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


def pct(change: float | None, base: float | None) -> float | None:
    if change is None or base in (None, 0):
        return None
    return change / base * 100.0


def fnum(value) -> float | None:  # noqa: ANN001
    """Parse a provider number (string or number). Empty / '.' / None -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s in ("", ".", "null", "None", "NaN"):
        return None
    return float(s)


class RateLimiter:
    """Simple async limiter: at most `per_minute` calls in any 60-second window."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._calls = [t for t in self._calls if now - t < 60]
                if len(self._calls) < self.per_minute:
                    self._calls.append(now)
                    return
                await asyncio.sleep(60 - (now - self._calls[0]) + 0.05)


class DailyBudget:
    """Counts calls per UTC day for providers with a daily cap (e.g. Twelve Data 800/day)."""

    def __init__(self, per_day: int) -> None:
        self.per_day = per_day
        self._day: str = ""
        self.used = 0

    def take(self, n: int = 1) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self.used = today, 0
        if self.used + n > self.per_day:
            raise ProviderRateLimited(f"daily budget of {self.per_day} calls used up (resets at 00:00 UTC)")
        self.used += n


OnUpdate = Callable[[str, QuoteData], None]


class Provider:
    """Base class. Subclasses set the class attributes and implement what they support."""

    name: str = "base"
    label: str = "Base"
    kind: str = "poll"  # stream | poll | daily
    poll_interval: float = 60.0  # seconds between quote polls
    expected_delay: float = 0.0  # typical delay of this source, seconds (for STALE thresholds)
    unofficial: bool = False
    requires_key: bool = False
    # How long fetched candles stay fresh before being fetched again (seconds), per timeframe.
    candle_ttl: dict[str, float] = {"5m": 120, "1h": 600, "1d": 7200}
    close_only: bool = False  # candles carry only a close value (daily official series)

    def __init__(self, http: httpx.AsyncClient, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key) if self.requires_key else True

    async def get_quote(self, symbol: str) -> QuoteData:
        raise UnsupportedError(f"{self.label} has no quote endpoint")

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[CandleData]:
        raise UnsupportedError(f"{self.label} has no candles")

    @property
    def can_stream(self) -> bool:
        return False

    def serves(self, symbol: str) -> bool:
        """Whether this provider can deliver quotes for the symbol in its current mode."""
        return True

    async def stream(self, symbols: set[str], on_update: OnUpdate, on_connected: Callable[[], None]) -> None:
        """Run until the connection drops (then return/raise; the caller reconnects)."""
        raise UnsupportedError(f"{self.label} has no stream")

    async def _get_json(self, url: str, **kw):  # noqa: ANN202
        try:
            r = await self.http.get(url, **kw)
        except httpx.TimeoutException:
            raise ProviderError(f"{self.label}: request timed out") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.label}: network error ({type(e).__name__})") from None
        if r.status_code == 429:
            raise ProviderRateLimited(f"{self.label}: rate limited (HTTP 429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"{self.label}: access refused (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise ProviderError(f"{self.label}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise ProviderError(f"{self.label}: response was not JSON") from None

    async def _get_text(self, url: str, **kw) -> str:
        try:
            r = await self.http.get(url, **kw)
        except httpx.TimeoutException:
            raise ProviderError(f"{self.label}: request timed out") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.label}: network error ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise ProviderError(f"{self.label}: HTTP {r.status_code}")
        return r.text


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0), headers={"User-Agent": USER_AGENT},
                             follow_redirects=True, limits=httpx.Limits(max_connections=20))

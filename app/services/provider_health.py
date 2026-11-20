"""Provider health tracking: last success, last error, error counts, cooldowns, manual disable.

Errors put a (provider, symbol) pair into an exponential-backoff cooldown (5 s, 10 s, 20 s ... max 5 min),
during which the next source in the asset's chain is used. Rate-limit errors cool down the whole provider.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from app.timeutil import utcnow

MAX_BACKOFF = 300.0


@dataclass
class ProviderHealth:
    name: str
    label: str
    configured: bool = True
    unofficial: bool = False
    manually_disabled: bool = False
    last_success: datetime | None = None
    last_error: datetime | None = None
    error_message: str | None = None
    error_count: int = 0  # total since start
    consecutive_errors: int = 0
    provider_cooldown_until: float = 0.0
    symbol_fail: dict[str, int] = field(default_factory=dict)
    symbol_cooldown_until: dict[str, float] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.configured:
            return "NOT_CONFIGURED"
        if self.manually_disabled:
            return "DISABLED"
        if self.last_success is None and self.last_error is None:
            return "IDLE"
        if self.consecutive_errors == 0:
            return "OK"
        return "DOWN" if self.consecutive_errors >= 3 else "DEGRADED"

    def usable(self) -> bool:
        return self.configured and not self.manually_disabled and time.monotonic() >= self.provider_cooldown_until

    def symbol_available(self, symbol: str) -> bool:
        return self.usable() and time.monotonic() >= self.symbol_cooldown_until.get(symbol, 0.0)

    def record_success(self, symbol: str | None = None) -> None:
        self.last_success = utcnow()
        self.consecutive_errors = 0
        if symbol is not None:
            self.symbol_fail.pop(symbol, None)
            self.symbol_cooldown_until.pop(symbol, None)

    def record_error(self, message: str, symbol: str | None = None, provider_wide: bool = False) -> None:
        self.last_error = utcnow()
        self.error_message = message[:300]
        self.error_count += 1
        self.consecutive_errors += 1
        if symbol is not None:
            n = self.symbol_fail.get(symbol, 0) + 1
            self.symbol_fail[symbol] = n
            self.symbol_cooldown_until[symbol] = time.monotonic() + min(MAX_BACKOFF, 5.0 * 2 ** (n - 1))
        if provider_wide:
            self.provider_cooldown_until = time.monotonic() + min(MAX_BACKOFF, 15.0 * 2 ** min(self.consecutive_errors - 1, 5))

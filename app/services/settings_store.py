"""Settings page storage.

- App settings you change on the Settings page (AI models, daily AI limit, news refresh interval,
  automatic breakdowns per hour) are saved in the database (app_settings) and applied immediately.
- Server settings (LAN access, port, unofficial sources) live in .env because they are read at start-up;
  the page updates only that one line in .env and says a restart is needed. Keys are never touched.
- Watchlist: switch assets on/off, reorder, add a US stock/ETF or a crypto coin.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select

from app.config import ENV_FILE, Settings
from app.db.session import Database
from app.models.tables import AppSetting, Asset
from app.services import market_hours as mh
from app.services.assets import E
from app.timeutil import utcnow

OVERRIDABLE: dict[str, type] = {"gemini_model_fast": str, "gemini_model_smart": str, "gemini_daily_call_limit": int,
                                "news_interval_min": int, "auto_breakdowns_per_hour": int}
LIMITS = {"gemini_daily_call_limit": (10, 5000), "news_interval_min": (2, 120), "auto_breakdowns_per_hour": (0, 20)}
ENV_EDITABLE = {"LAN_ACCESS", "PORT", "ALLOW_UNOFFICIAL_SOURCES"}


def load_overrides(db: Database, settings: Settings) -> dict[str, Any]:
    with db.session() as s:
        row = s.get(AppSetting, "settings_overrides")
        data = dict(row.value) if row and isinstance(row.value, dict) else {}
    for k, v in data.items():
        if k in OVERRIDABLE and v not in (None, ""):
            setattr(settings, k, OVERRIDABLE[k](v))
    return data


def save_overrides(db: Database, settings: Settings, changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in OVERRIDABLE or v is None:
            continue
        val = OVERRIDABLE[k](v) if v != "" else ""
        if k in LIMITS and val != "":
            lo, hi = LIMITS[k]
            if not lo <= val <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
        clean[k] = val
    with db.session() as s:
        row = s.get(AppSetting, "settings_overrides")
        data = dict(row.value) if row and isinstance(row.value, dict) else {}
        data.update(clean)
        if row is None:
            s.add(AppSetting(key="settings_overrides", value=data, updated_at=utcnow()))
        else:
            row.value, row.updated_at = data, utcnow()
    for k, v in clean.items():
        if v != "":
            setattr(settings, k, v)
    return clean


def set_env_line(name: str, value: str) -> None:
    """Change one setting line in .env (only LAN_ACCESS / PORT / ALLOW_UNOFFICIAL_SOURCES). Other lines are untouched."""
    if name not in ENV_EDITABLE:
        raise ValueError("This setting cannot be changed here")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,20}", value):
        raise ValueError("Invalid value")
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    pattern = re.compile(rf"^{name}=.*$", re.M)
    text = pattern.sub(f"{name}={value}", text) if pattern.search(text) else text.rstrip("\n") + f"\n{name}={value}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- watchlist
def watchlist(db: Database) -> list[dict[str, Any]]:
    with db.session() as s:
        return [{"symbol": a.symbol, "name": a.name, "asset_class": a.asset_class, "enabled": a.enabled,
                 "custom": bool((a.provider_symbols or {}).get("custom"))}
                for a in s.scalars(select(Asset).order_by(Asset.sort_order, Asset.id))]


def set_enabled(db: Database, symbol: str, enabled: bool) -> None:
    with db.session() as s:
        a = s.scalar(select(Asset).where(Asset.symbol == symbol))
        if a is None:
            raise ValueError("Unknown asset")
        a.enabled = enabled


def move(db: Database, symbol: str, direction: int) -> None:
    with db.session() as s:
        assets = list(s.scalars(select(Asset).order_by(Asset.sort_order, Asset.id)))
        idx = next((i for i, a in enumerate(assets) if a.symbol == symbol), None)
        if idx is None:
            raise ValueError("Unknown asset")
        j = idx + (1 if direction > 0 else -1)
        if 0 <= j < len(assets):
            assets[idx], assets[j] = assets[j], assets[idx]
        for i, a in enumerate(assets):
            a.sort_order = i


def add_asset(db: Database, kind: str, symbol: str, name: str) -> str:
    """Add a US stock/ETF (Finnhub -> Twelve Data -> Yahoo) or a crypto coin (Binance -> Gate.io), quoted in USDT."""
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", sym):
        raise ValueError("Symbol must be 1-10 letters/numbers")
    name = (name or sym).strip()[:80]
    if kind == "stock":
        chains = {"quote": [E("finnhub", sym, "US consolidated trades"), E("twelvedata", sym, "US exchange quote"),
                            E("yahoo", sym, "Yahoo quote (unofficial)")],
                  "candles": [E("twelvedata", sym, "US exchange candles"), E("yahoo", sym, "Yahoo candles (unofficial)")],
                  "custom": True}
        cls, sched, dec, cur = "stock", mh.US_EQUITY, 2, "USD"
    elif kind == "crypto":
        chains = {"quote": [E("binance", f"{sym}USDT", f"{sym}/USDT on Binance"), E("gateio", f"{sym}_USDT", f"{sym}/USDT on Gate.io")],
                  "candles": [E("binance", f"{sym}USDT", f"{sym}/USDT on Binance"), E("gateio", f"{sym}_USDT", f"{sym}/USDT on Gate.io")],
                  "custom": True}
        cls, sched, dec, cur = "crypto", mh.CRYPTO, 4, "USD"
    else:
        raise ValueError("Kind must be stock or crypto")
    with db.session() as s:
        if s.scalar(select(Asset.id).where(Asset.symbol == sym)):
            raise ValueError(f"{sym} is already in the list")
        order = (s.scalar(select(func.max(Asset.sort_order))) or 0) + 1
        s.add(Asset(symbol=sym, name=name, asset_class=cls, provider_symbols=chains, market_schedule=sched, currency=cur,
                    decimals=dec, enabled=True, sort_order=order, created_at=utcnow()))
    return sym


def remove_custom(db: Database, symbol: str) -> None:
    with db.session() as s:
        a = s.scalar(select(Asset).where(Asset.symbol == symbol))
        if a is None or not (a.provider_symbols or {}).get("custom"):
            raise ValueError("Only assets you added yourself can be removed (others can be switched off)")
        s.delete(a)

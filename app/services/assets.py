"""Default asset list and its data-source chains.

Each asset has an ordered chain of sources for its price ("quote") and for its chart ("candles").
The first usable source is used; if it fails, the next one takes over automatically.
"note" says exactly which instrument a source measures (e.g. futures vs spot), so the UI can be honest.
`proxy: true` marks a stand-in instrument (e.g. the PAX Gold token for gold's chart).

The list is stored in the `assets` table on first start. Later seed versions update the chains of
the default assets (so source fixes reach existing databases) without touching enabled/disabled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.db.session import Database
from app.models.tables import AppSetting, Asset
from app.services import market_hours as mh
from app.timeutil import utcnow

log = logging.getLogger(__name__)

SEED_VERSION = 3  # v2: Finnhub OANDA streams first for gold/silver; weekly limit for the Fed dollar index
#                  v3: Yahoo USD history for crypto (full history since 2014 for BTC; Binance starts Aug 2017)


def E(provider: str, symbol: str, note: str, proxy: bool = False, max_age_days: int | None = None) -> dict[str, Any]:  # noqa: N802
    e: dict[str, Any] = {"provider": provider, "symbol": symbol, "note": note, "proxy": proxy}
    if max_age_days is not None:
        e["max_age_days"] = max_age_days
    return e


def _crypto(sym: str, name: str, binance: str, gate: str, gecko: str, decimals: int = 2) -> dict[str, Any]:
    # Charts up to 1Y come from Binance (the live price's own market). YTD / 5Y / MAX use Yahoo's daily USD history,
    # the longest free one (measured 24 Nov 2026: BTC-USD from 17 Sep 2014, ETH/BNB/XRP from 9 Nov 2017,
    # SOL from 10 Apr 2020; Binance starts 17 Aug 2017 for BTC). Yahoo is also the last chart fallback.
    return dict(symbol=sym, name=name, asset_class="crypto", schedule=mh.CRYPTO, currency="USD", decimals=decimals,
                quote=[E("binance", binance, f"{binance[:-4]}/USDT on Binance"),
                       E("gateio", gate, f"{gate.replace('_', '/')} on Gate.io"),
                       E("coingecko", gecko, "CoinGecko average price in USD")],
                candles=[E("binance", binance, f"{binance[:-4]}/USDT on Binance"),
                         E("gateio", gate, f"{gate.replace('_', '/')} on Gate.io"),
                         E("yahoo", f"{sym}-USD", f"{sym} in US dollars, daily (Yahoo, unofficial)")])


def _stock(sym: str, name: str, asset_class: str = "stock") -> dict[str, Any]:
    return dict(symbol=sym, name=name, asset_class=asset_class, schedule=mh.US_EQUITY, currency="USD", decimals=2,
                quote=[E("finnhub", sym, "US consolidated trades"), E("twelvedata", sym, "US exchange quote"),
                       E("yahoo", sym, "Yahoo quote (unofficial)")],
                candles=[E("twelvedata", sym, "US exchange candles"), E("yahoo", sym, "Yahoo candles (unofficial)")])


DEFAULT_ASSETS: list[dict[str, Any]] = [
    _crypto("BTC", "Bitcoin", "BTCUSDT", "BTC_USDT", "bitcoin"),
    _crypto("ETH", "Ethereum", "ETHUSDT", "ETH_USDT", "ethereum"),
    _crypto("SOL", "Solana", "SOLUSDT", "SOL_USDT", "solana"),
    _crypto("BNB", "BNB", "BNBUSDT", "BNB_USDT", "binancecoin"),
    _crypto("XRP", "XRP", "XRPUSDT", "XRP_USDT", "ripple", decimals=4),
    # Finnhub's free key streams OANDA spot quotes live (verified 20 Nov 2026), so it goes first;
    # gold-api.com (checked every 15 s) is the backup.
    dict(symbol="XAU", name="Gold (spot)", asset_class="metal", schedule=mh.SPOT, currency="USD", decimals=2,
         quote=[E("finnhub", "OANDA:XAU_USD", "OANDA spot gold (CFD), USD per troy ounce"),
                E("goldapi", "XAU", "Spot gold, USD per troy ounce")],
         candles=[E("binance", "PAXGUSDT", "PAX Gold token (1 token = 1 oz of gold) in USDT", proxy=True),
                  E("yahoo", "GC=F", "COMEX gold futures (unofficial)", proxy=True)]),
    dict(symbol="XAG", name="Silver (spot)", asset_class="metal", schedule=mh.SPOT, currency="USD", decimals=3,
         quote=[E("finnhub", "OANDA:XAG_USD", "OANDA spot silver (CFD), USD per troy ounce"),
                E("goldapi", "XAG", "Spot silver, USD per troy ounce")],
         candles=[E("yahoo", "SI=F", "COMEX silver futures (unofficial)", proxy=True)]),
    dict(symbol="WTI", name="WTI crude oil", asset_class="energy", schedule=mh.FUTURES, currency="USD", decimals=2,
         quote=[E("finnhub", "OANDA:WTICO_USD", "OANDA WTI crude (CFD)"),
                E("yahoo", "CL=F", "NYMEX WTI front-month futures (unofficial)"),
                E("fred", "DCOILWTICO", "EIA WTI spot price, daily (official)")],
         candles=[E("yahoo", "CL=F", "NYMEX WTI front-month futures (unofficial)"),
                  E("fred", "DCOILWTICO", "EIA WTI spot price, daily (official)")]),
    dict(symbol="BRENT", name="Brent crude oil", asset_class="energy", schedule=mh.FUTURES, currency="USD", decimals=2,
         quote=[E("finnhub", "OANDA:BCO_USD", "OANDA Brent crude (CFD)"),
                E("yahoo", "BZ=F", "ICE Brent front-month futures (unofficial)"),
                E("fred", "DCOILBRENTEU", "EIA Brent spot price, daily (official)")],
         candles=[E("yahoo", "BZ=F", "ICE Brent front-month futures (unofficial)"),
                  E("fred", "DCOILBRENTEU", "EIA Brent spot price, daily (official)")]),
    dict(symbol="NATGAS", name="Natural gas (Henry Hub)", asset_class="energy", schedule=mh.FUTURES, currency="USD", decimals=3,
         quote=[E("finnhub", "OANDA:NATGAS_USD", "OANDA natural gas (CFD)"),
                E("yahoo", "NG=F", "NYMEX natural gas front-month futures (unofficial)"),
                E("fred", "DHHNGSP", "EIA Henry Hub spot price, daily (official)")],
         candles=[E("yahoo", "NG=F", "NYMEX natural gas front-month futures (unofficial)"),
                  E("fred", "DHHNGSP", "EIA Henry Hub spot price, daily (official)")]),
    _stock("AAPL", "Apple"),
    _stock("MSFT", "Microsoft"),
    _stock("NVDA", "NVIDIA"),
    _stock("AMZN", "Amazon"),
    _stock("GOOGL", "Alphabet (Class A)"),
    _stock("META", "Meta Platforms"),
    _stock("TSLA", "Tesla"),
    _stock("SPY", "SPDR S&P 500 ETF", "etf"),
    _stock("QQQ", "Invesco QQQ (Nasdaq-100 ETF)", "etf"),
    dict(symbol="SPX", name="S&P 500 index", asset_class="index", schedule=mh.US_EQUITY, currency="USD", decimals=2,
         quote=[E("fred", "SP500", "S&P 500 daily close (FRED, official)"),
                E("yahoo", "^GSPC", "S&P 500 index (unofficial)")],
         candles=[E("fred", "SP500", "S&P 500 daily close (FRED, official)"), E("yahoo", "^GSPC", "S&P 500 index (unofficial)")]),
    dict(symbol="IXIC", name="Nasdaq Composite index", asset_class="index", schedule=mh.US_EQUITY, currency="USD", decimals=2,
         quote=[E("fred", "NASDAQCOM", "Nasdaq Composite daily close (FRED, official)"),
                E("yahoo", "^IXIC", "Nasdaq Composite (unofficial)")],
         candles=[E("fred", "NASDAQCOM", "Nasdaq Composite daily close (FRED, official)"),
                  E("yahoo", "^IXIC", "Nasdaq Composite (unofficial)")]),
    dict(symbol="DXY", name="US Dollar Index (DXY)", asset_class="macro", schedule=mh.FUTURES, currency="", decimals=3,
         quote=[E("yahoo", "DX-Y.NYB", "ICE US Dollar Index futures-exchange value (unofficial)")],
         candles=[E("yahoo", "DX-Y.NYB", "ICE US Dollar Index (unofficial)")],
         unavailable_reason="No free official source publishes the ICE US Dollar Index (DXY)."),
    dict(symbol="US10Y", name="US 10-year Treasury yield", asset_class="macro", schedule=mh.US_EQUITY, currency="%", decimals=2,
         quote=[E("treasury", "10 Yr", "US Treasury par yield, end of day (official)"),
                E("yahoo", "^TNX", "CBOE 10-year yield index (unofficial)")],
         candles=[E("treasury", "10 Yr", "US Treasury par yield, end of day (official)"),
                  E("yahoo", "^TNX", "CBOE 10-year yield index (unofficial)")]),
    dict(symbol="USDBROAD", name="Fed Broad Dollar Index (not DXY)", asset_class="macro", schedule=mh.US_EQUITY,
         currency="", decimals=2,
         quote=[E("fred", "DTWEXBGS", "Federal Reserve nominal broad dollar index, daily values published weekly (official)",
                  max_age_days=10)],
         candles=[E("fred", "DTWEXBGS", "Federal Reserve nominal broad dollar index, daily (official)")]),
]

CLASS_ORDER = ["crypto", "metal", "energy", "stock", "etf", "index", "macro"]
CLASS_LABELS = {"crypto": "Crypto (24/7)", "metal": "Precious metals", "energy": "Energy", "stock": "Stocks",
                "etf": "ETFs", "index": "Indexes", "macro": "Macro"}


def _chains(d: dict[str, Any]) -> dict[str, Any]:
    return {"quote": d["quote"], "candles": d["candles"], "unavailable_reason": d.get("unavailable_reason")}


def seed_assets(db: Database) -> None:
    """Insert default assets on first start; refresh default chains when SEED_VERSION increases."""
    with db.session() as s:
        current = s.get(AppSetting, "assets_seed_version")
        have = {a.symbol: a for a in s.scalars(select(Asset))}
        if not have:
            for i, d in enumerate(DEFAULT_ASSETS):
                s.add(Asset(symbol=d["symbol"], name=d["name"], asset_class=d["asset_class"], provider_symbols=_chains(d),
                            market_schedule=d["schedule"], currency=d["currency"], decimals=d["decimals"],
                            enabled=True, sort_order=i))
            log.info("Seeded %d default assets", len(DEFAULT_ASSETS))
        elif current is None or int(current.value) < SEED_VERSION:
            for i, d in enumerate(DEFAULT_ASSETS):
                a = have.get(d["symbol"])
                if a is None:
                    s.add(Asset(symbol=d["symbol"], name=d["name"], asset_class=d["asset_class"], provider_symbols=_chains(d),
                                market_schedule=d["schedule"], currency=d["currency"], decimals=d["decimals"],
                                enabled=True, sort_order=1000 + i))
                else:
                    a.provider_symbols = _chains(d)
                    a.market_schedule = d["schedule"]
                    a.decimals = d["decimals"]
            log.info("Updated default asset sources to seed v%d", SEED_VERSION)
        if current is None:
            s.add(AppSetting(key="assets_seed_version", value=SEED_VERSION, updated_at=utcnow()))
        else:
            current.value = SEED_VERSION
            current.updated_at = utcnow()


@dataclass
class ChainEntry:
    provider: str
    symbol: str
    note: str = ""
    proxy: bool = False
    max_age_days: int | None = None


@dataclass
class AssetRT:
    """Runtime view of an asset row."""

    id: int
    symbol: str
    name: str
    asset_class: str
    schedule: str
    currency: str
    decimals: int
    enabled: bool
    sort_order: int
    quote_chain: list[ChainEntry] = field(default_factory=list)
    candle_chain: list[ChainEntry] = field(default_factory=list)
    unavailable_reason: str | None = None


def _entries(raw: list[dict[str, Any]] | None) -> list[ChainEntry]:
    return [ChainEntry(provider=e["provider"], symbol=e["symbol"], note=e.get("note", ""), proxy=bool(e.get("proxy")),
                       max_age_days=e.get("max_age_days"))
            for e in raw or []]


def load_assets(db: Database) -> list[AssetRT]:
    with db.session() as s:
        rows = s.scalars(select(Asset).order_by(Asset.sort_order, Asset.id)).all()
    out = []
    for a in rows:
        ps = a.provider_symbols or {}
        out.append(AssetRT(id=a.id, symbol=a.symbol, name=a.name, asset_class=a.asset_class, schedule=a.market_schedule,
                           currency=a.currency, decimals=a.decimals, enabled=a.enabled, sort_order=a.sort_order,
                           quote_chain=_entries(ps.get("quote")), candle_chain=_entries(ps.get("candles")),
                           unavailable_reason=ps.get("unavailable_reason")))
    return out

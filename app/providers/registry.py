"""Builds the provider objects from settings."""
from __future__ import annotations

import httpx

from app.config import Settings
from app.providers.base import Provider
from app.providers.binance import BinanceProvider
from app.providers.coingecko import CoinGeckoProvider
from app.providers.finnhub import FinnhubProvider
from app.providers.fred import FredProvider
from app.providers.gateio import GateioProvider
from app.providers.goldapi import GoldApiProvider
from app.providers.treasury import TreasuryProvider
from app.providers.twelvedata import TwelveDataProvider
from app.providers.yahoo import YahooProvider

ALL_PROVIDER_NAMES = ("binance", "gateio", "coingecko", "goldapi", "finnhub", "twelvedata", "yahoo", "fred", "treasury")


def build_providers(settings: Settings, http: httpx.AsyncClient) -> dict[str, Provider]:
    """All providers this install may use. Yahoo is left out when unofficial sources are switched off."""
    providers: dict[str, Provider] = {
        "binance": BinanceProvider(http),
        "gateio": GateioProvider(http),
        "coingecko": CoinGeckoProvider(http, settings.coingecko_api_key),
        "goldapi": GoldApiProvider(http),
        "finnhub": FinnhubProvider(http, settings.finnhub_api_key),
        "twelvedata": TwelveDataProvider(http, settings.twelve_data_api_key),
        "fred": FredProvider(http),
        "treasury": TreasuryProvider(http),
    }
    if settings.allow_unofficial_sources:
        providers["yahoo"] = YahooProvider(http)
    return providers

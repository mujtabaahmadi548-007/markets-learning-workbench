"""Provider parsers against REAL responses recorded on 20 Nov 2026 (tests/fixtures, via scripts/capture_fixtures.py).
Finnhub samples are the documented examples from finnhub.io/docs (no key was used for recording)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.providers import binance, coingecko, finnhub, fred, gateio, goldapi, treasury, twelvedata, yahoo
from app.providers.base import ProviderError

FX = Path(__file__).parent / "fixtures"


def load(name: str):  # noqa: ANN201
    text = (FX / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def test_binance_ticker_and_stream_and_klines() -> None:
    d = load("binance_ticker_24hr.json")
    q = binance.parse_ticker_24hr(d)
    assert q.price == float(d["lastPrice"]) and q.quote_currency == "USDT" and q.change_basis == "24h"
    assert q.change_pct == float(d["priceChangePercent"])
    msgs = load("binance_ws_miniticker.json")
    sym, q2 = binance.parse_mini_ticker(msgs[0]["data"])
    data = msgs[0]["data"]
    assert sym == data["s"] and q2.price == float(data["c"]) and q2.prev_close == float(data["o"])
    assert q2.change == pytest.approx(float(data["c"]) - float(data["o"]))
    kl = binance.parse_klines(load("binance_klines_1h.json"))
    assert len(kl) == 5 and all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in kl)


def test_gateio_rest_ws_candles() -> None:
    now = datetime.now(timezone.utc)
    q = gateio.parse_rest_tickers(load("gateio_ticker.json"), now)
    assert q.price > 0 and q.source_timestamp == now and q.quote_currency == "USDT"
    parsed = gateio.parse_ws_ticker(load("gateio_ws_tickers.json")[-1])
    assert parsed is not None and parsed[0] == "BTC_USDT"
    candles = gateio.parse_candles(load("gateio_candles_1h.json"))
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in candles)


def test_coingecko() -> None:
    d = load("coingecko_simple_price.json")
    q = coingecko.parse_simple_price(d, "bitcoin")
    assert q.price == float(d["bitcoin"]["usd"]) and q.quote_currency == "USD"
    assert q.change_pct == pytest.approx(d["bitcoin"]["usd_24h_change"])
    with pytest.raises(ProviderError):
        coingecko.parse_simple_price(d, "dogecoin")


def test_goldapi() -> None:
    d = load("goldapi_xau.json")
    q = goldapi.parse_price(d)
    assert q.price == pytest.approx(d["price"]) and q.change is None  # no previous close from this source
    assert q.source_timestamp.tzinfo is not None


def test_yahoo_quote_and_candles() -> None:
    d = load("yahoo_chart_clf_5m.json")
    q = yahoo.parse_quote(d)
    meta = d["chart"]["result"][0]["meta"]
    assert q.price == meta["regularMarketPrice"] and q.prev_close == meta["previousClose"]
    assert q.change == pytest.approx(meta["regularMarketPrice"] - meta["previousClose"])
    candles = yahoo.parse_candles(d)
    assert len(candles) > 50 and candles[0].ts < candles[-1].ts
    with pytest.raises(ProviderError):
        yahoo.parse_quote({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}})


def test_fred_and_treasury_daily() -> None:
    vals = fred.parse_csv(load("fred_dcoilwtico.csv"))
    q = fred.quote_from_series(vals)
    assert q.price == vals[-1][1] and q.prev_close == vals[-2][1] and q.change_basis == "prev_obs"
    assert q.source_timestamp == datetime(vals[-1][0].year, vals[-1][0].month, vals[-1][0].day, tzinfo=timezone.utc)
    ty = treasury.parse_csv(load("treasury_2026.csv"), "10 Yr")
    assert ty == sorted(ty) and 0 < ty[-1][1] < 20
    with pytest.raises(ProviderError):
        treasury.parse_csv(load("treasury_2026.csv"), "99 Yr")


def test_twelvedata() -> None:
    d = load("twelvedata_quote_aapl_demo.json")
    q = twelvedata.parse_quote(d)
    assert q.price == float(d["close"]) and q.prev_close == float(d["previous_close"])
    ts = twelvedata.parse_time_series(load("twelvedata_timeseries_aapl_demo.json"))
    assert ts and ts == sorted(ts, key=lambda c: c.ts)
    with pytest.raises(ProviderError):
        twelvedata.parse_quote({"code": 401, "message": "Invalid API key", "status": "error"})


def test_finnhub_documented_samples() -> None:
    q = finnhub.parse_quote({"c": 261.74, "h": 263.31, "l": 260.68, "o": 261.07, "pc": 259.45, "t": 1582641000})
    assert q.price == 261.74 and q.prev_close == 259.45 and q.day_high == 263.31
    with pytest.raises(ProviderError):
        finnhub.parse_quote({"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})  # unknown symbol
    trades = finnhub.parse_trades({"data": [{"p": 7296.89, "s": "BINANCE:BTCUSDT", "t": 1575526691134, "v": 0.011467},
                                            {"p": 7297.10, "s": "BINANCE:BTCUSDT", "t": 1575526691200, "v": 0.01}],
                                   "type": "trade"})
    assert trades["BINANCE:BTCUSDT"].price == 7297.10  # newest trade wins
    assert finnhub.parse_trades({"type": "ping"}) == {}


def test_finnhub_stream_off_for_website_build() -> None:
    p = finnhub.FinnhubProvider(http=None, api_key="test-key")
    assert p.can_stream and p.serves("OANDA:XAU_USD") and p.serves("AAPL")
    p.stream_enabled = False  # website build: never take the one stream connection from the user's app
    assert not p.can_stream and not p.serves("OANDA:XAU_USD") and p.serves("AAPL")

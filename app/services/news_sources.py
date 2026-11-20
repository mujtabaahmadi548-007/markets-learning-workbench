"""News feeds (all verified working from this PC on 20 Nov 2026) and source-quality tiers.

`filtered=True` feeds are general news: an item is kept only if the keyword tagger links it to a tracked asset.
Not used (checked 20 Nov 2026): Reuters (no RSS any more -> Google News search below), Kitco (no RSS ->
Google News search), Bitcoin Magazine, Nasdaq and MINING.COM (403), FXStreet (unreachable), MarketWatch
"Market Pulse" and "Real-time headlines" (stopped updating in 2025), Yahoo Finance index feed (404).
Google News searches are limited to their 25 newest items each, and obvious non-news pages
(prediction-market pages, "price prediction" clickbait) are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus

GOOGLE_NEWS_MAX_ITEMS = 25
JUNK_TITLE = re.compile(r"prediction market|price predictions?\b|price range on .+ at \d|\bshould you buy\b|"
                        r"\bto buy (now|today|before)\b|\btop \d+ (stocks|cryptos?|coins) to\b", re.I)
JUNK_SOURCES = {"Robinhood", "Polymarket", "Kalshi"}
SOURCE_ALIASES = {"Bloomberg.com": "Bloomberg", "WSJ": "The Wall Street Journal", "Reuters.com": "Reuters",
                  "CNBC.com": "CNBC", "Kitco NEWS": "Kitco", "MarketWatch.com": "MarketWatch", "Yahoo": "Yahoo Finance",
                  "finance.yahoo.com": "Yahoo Finance", "Investing.com India": "Investing.com", "FT": "Financial Times"}


def normalise_source(name: str) -> str:
    return SOURCE_ALIASES.get(name.strip(), name.strip())


def is_junk(title: str, source: str) -> bool:
    return source in JUNK_SOURCES or bool(JUNK_TITLE.search(title))


@dataclass(frozen=True)
class NewsSource:
    key: str
    name: str
    url: str
    filtered: bool = False  # keep only items that match a tracked asset
    official: bool = False
    google_news: bool = False


def gnews(key: str, query: str) -> NewsSource:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:2d&hl=en-US&gl=US&ceid=US:en"
    return NewsSource(key=f"gn_{key}", name="Google News", url=url, google_news=True)


FEEDS: list[NewsSource] = [
    # Crypto
    NewsSource("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss"),
    NewsSource("theblock", "The Block", "https://www.theblock.co/rss.xml"),
    NewsSource("decrypt", "Decrypt", "https://decrypt.co/feed"),
    NewsSource("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    # Energy / metals
    NewsSource("oilprice", "OilPrice.com", "https://oilprice.com/rss/main"),
    NewsSource("eia", "US EIA", "https://www.eia.gov/rss/todayinenergy.xml", official=True),
    # Markets / business
    NewsSource("bloomberg_markets", "Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    NewsSource("bloomberg_econ", "Bloomberg", "https://feeds.bloomberg.com/economics/news.rss"),
    NewsSource("wsj_markets", "The Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    NewsSource("marketwatch", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    NewsSource("cnbc_top", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    NewsSource("cnbc_finance", "CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    NewsSource("cnbc_economy", "CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    NewsSource("cnbc_energy", "CNBC", "https://www.cnbc.com/id/19836768/device/rss/rss.html"),
    NewsSource("investing", "Investing.com", "https://www.investing.com/rss/news.rss", filtered=True),
    NewsSource("investing_commod", "Investing.com", "https://www.investing.com/rss/news_11.rss"),
    NewsSource("investing_crypto", "Investing.com", "https://www.investing.com/rss/news_301.rss"),
    NewsSource("seekingalpha", "Seeking Alpha", "https://seekingalpha.com/market_currents.xml", filtered=True),
    NewsSource("benzinga", "Benzinga", "https://www.benzinga.com/feed", filtered=True),
    NewsSource("guardian_business", "The Guardian", "https://www.theguardian.com/uk/business/rss", filtered=True),
    NewsSource("bbc_business", "BBC News", "https://feeds.bbci.co.uk/news/business/rss.xml", filtered=True),
    # Official
    NewsSource("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", official=True),
    NewsSource("fed_monetary", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", official=True),
    NewsSource("ecb", "European Central Bank", "https://www.ecb.europa.eu/rss/press.html", official=True, filtered=True),
] + [
    # Yahoo Finance per-stock headlines
    NewsSource(f"yahoo_{s.lower()}", "Yahoo Finance", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={s}&region=US&lang=en-US")
    for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "SPY", "QQQ")
]

GOOGLE_NEWS: list[NewsSource] = [
    gnews("gold", "gold price"),
    gnews("silver", "silver price"),
    gnews("kitco", "site:kitco.com gold"),
    gnews("oil", "crude oil OPEC"),
    gnews("natgas", "natural gas prices"),
    gnews("bitcoin", "Bitcoin price"),
    gnews("btc_etf", "Bitcoin ETF"),
    gnews("ethereum", "Ethereum price"),
    gnews("fed", "Federal Reserve interest rates"),
    gnews("yields", "Treasury yields"),
    gnews("dollar", "dollar index"),
    gnews("reuters", "site:reuters.com markets"),
    gnews("nvda", "NVIDIA stock"),
    gnews("aapl", "Apple stock"),
    gnews("tsla", "Tesla stock"),
    gnews("msft", "Microsoft stock"),
    gnews("amzn", "Amazon stock"),
    gnews("googl", "Alphabet stock"),
    gnews("meta", "Meta Platforms stock"),
]

ALL_SOURCES: dict[str, NewsSource] = {s.key: s for s in FEEDS + GOOGLE_NEWS}

# Source quality (0..1) used by the importance score.
OFFICIAL = {"Federal Reserve", "US EIA", "European Central Bank", "U.S. Energy Information Administration",
            "Bureau of Labor Statistics", "U.S. Department of the Treasury"}
TIER1 = {"Bloomberg", "Reuters", "The Wall Street Journal", "Financial Times", "CNBC", "Associated Press", "AP News",
         "The Economist", "Bloomberg.com", "WSJ"}
TIER2 = {"MarketWatch", "Yahoo Finance", "The Guardian", "BBC News", "CoinDesk", "The Block", "Cointelegraph", "Decrypt",
         "OilPrice.com", "Kitco NEWS", "Kitco", "Investing.com", "Barron's", "Fortune", "Business Insider", "Forbes",
         "MINING.COM", "Seeking Alpha", "Benzinga", "Axios", "The New York Times", "CNN", "Nikkei Asia", "Fox Business"}


def source_quality(name: str) -> float:
    if name in OFFICIAL:
        return 1.0
    if name in TIER1:
        return 0.9
    if name in TIER2:
        return 0.7
    return 0.5

"""News: normalisation, tagging, de-duplication, story grouping, feed parsing."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.config import load_settings
from app.db.migrations import init_db
from app.db.session import Database
from app.models.tables import ArticleAsset, NewsArticle
from app.providers import rss
from app.services import tagger
from app.services.assets import seed_assets
from app.services.news import IngestStats, NewsService, base_importance
from app.services.news_sources import NewsSource
from app.services.textutil import canonical_url, clean_title, extract_article_text, jaccard, title_tokens
from app.ai.gemini_client import GeminiService
from app.timeutil import utcnow

SYMS = {"BTC": "Bitcoin", "XAU": "Gold", "WTI": "WTI", "BRENT": "Brent", "NVDA": "NVIDIA", "US10Y": "10Y", "DXY": "DXY",
        "META": "Meta", "AAPL": "Apple"}


def test_canonical_url_drops_tracking() -> None:
    assert canonical_url("https://WWW.Example.com/a/b/?utm_source=x&id=7#top") == "https://www.example.com/a/b?id=7"


def test_clean_title_removes_google_suffix() -> None:
    assert clean_title("Gold slips as dollar firms - Reuters", "Reuters") == "Gold slips as dollar firms"


def test_similarity() -> None:
    a = title_tokens("Gold falls as dollar strengthens ahead of Fed decision")
    b = title_tokens("Gold falls as the dollar strengthens ahead of the Fed decision")
    c = title_tokens("Nvidia shares jump after earnings beat")
    assert jaccard(a, b) >= 0.85 and jaccard(a, c) < 0.2


def test_keyword_tags() -> None:
    t = tagger.keyword_tags("Gold falls as Treasury yields climb after Fed minutes", None)
    assert t["XAU"] == 0.9 and t["US10Y"] >= 0.6 and "DXY" in t
    assert "XAU" not in tagger.keyword_tags("Goldman Sachs raises target for Nvidia", None)
    assert tagger.keyword_tags("Goldman Sachs raises target for Nvidia", None)["NVDA"] == 0.9
    assert "META" not in tagger.keyword_tags("A meta-analysis of studies", None)
    assert tagger.keyword_tags("OPEC+ agrees to extend output cuts", None).keys() >= {"WTI", "BRENT"}


def test_parse_feed_google_news_item() -> None:
    xml = b"""<?xml version="1.0"?><rss><channel><item><title>Gold slips - CNBC</title>
      <link>https://news.google.com/rss/articles/ABC?oc=5</link><pubDate>Thu, 19 Nov 2026 20:00:00 GMT</pubDate>
      <description>&lt;a href="x"&gt;Gold slips&lt;/a&gt;</description><source url="https://www.cnbc.com">CNBC</source></item></channel></rss>"""
    items = rss.parse_feed(xml, "Google News")
    assert items[0].title == "Gold slips" and items[0].source_name == "CNBC" and items[0].via_google_news
    assert items[0].description is None and items[0].published_at.isoformat().startswith("2026-11-19T20:00")


def test_feed_images() -> None:
    xml = b"""<?xml version="1.0"?><rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <item><title>A</title><link>https://a.com/1</link>
        <media:content url="https://img.a.com/small.jpg" medium="image" width="300"/>
        <media:content url="https://img.a.com/big.jpg" medium="image" width="1200"/></item>
      <item><title>B</title><link>https://b.com/1</link><media:thumbnail url="https://img.b.com/t.jpg"/></item>
      <item><title>C</title><link>https://c.com/1</link><enclosure url="https://img.c.com/e.png" type="image/png" length="1"/></item>
      <item><title>D</title><link>https://d.com/1</link><description>&lt;p&gt;&lt;img src="https://img.d.com/x.jpg"&gt;Text&lt;/p&gt;</description></item>
      <item><title>E</title><link>https://e.com/1</link><description>No picture</description></item>
    </channel></rss>"""
    imgs = [i.image_url for i in rss.parse_feed(xml, "X")]
    assert imgs == ["https://img.a.com/big.jpg", "https://img.b.com/t.jpg", "https://img.c.com/e.png", "https://img.d.com/x.jpg", None]


def test_og_image() -> None:
    from app.services.textutil import extract_og_image

    page = '<head><meta property="og:image" content="/img/hero.jpg?w=1200&amp;h=630"><meta name="twitter:image" content="https://x/y.png"></head>'
    assert extract_og_image(page, "https://news.example.com/a/b") == "https://news.example.com/img/hero.jpg?w=1200&h=630"
    assert extract_og_image('<meta content="https://x.com/1x1.gif" property="og:image">', "https://x.com") is None
    assert extract_og_image("<head></head>", "https://x.com") is None


def test_generic_images_are_not_used() -> None:
    from app.services.news import usable_image

    assert usable_image("https://cdn.decrypt.co/wp-content/uploads/2026/02/bitcoin-down.jpg")
    assert not usable_image("https://s.yimg.com/lo/mysterio/api/150afeec82b9/default.png")
    assert not usable_image("https://example.com/static/site-logo.png")
    assert not usable_image("https://x.com/a.jpg", {"https://x.com/a.jpg"})  # reused by many articles
    assert not usable_image(None)


def test_topic_for_fallback_illustration() -> None:
    from app.services.news import topic_for

    assert topic_for([{"symbol": "XAU", "confidence": 0.9, "asset_class": "metal"}]) == "gold"
    assert topic_for([{"symbol": "NVDA", "confidence": 0.9, "asset_class": "stock"},
                      {"symbol": "BTC", "confidence": 0.5, "asset_class": "crypto"}]) == "stocks"
    assert topic_for([], official=True) == "centralbank"
    assert topic_for([]) == "markets"


def test_extract_article_text_prefers_article_paragraphs() -> None:
    html = ("<html><nav><p>Menu item that is long enough to be counted as a paragraph here</p></nav><article>"
            + "".join(f"<p>Paragraph {i} of the real story, with enough words to pass the length filter easily.</p>" for i in range(12))
            + "</article><p>Subscribe to our newsletter for more updates and offers today!</p></html>")
    text = extract_article_text(html)
    assert "Paragraph 0" in text and "Menu item" not in text and "Subscribe" not in text


def make_service(tmp_path: Path) -> NewsService:
    db = Database(tmp_path / "n.db")
    init_db(db)
    seed_assets(db)
    return NewsService(db, http=None, gemini=GeminiService(load_settings(env_file=None), db), symbols=dict(SYMS))  # type: ignore[arg-type]


def item(title: str, url: str, desc: str | None = None, source: str = "CNBC", hours_ago: float = 1) -> rss.FeedItem:
    return rss.FeedItem(title=title, url=url, description=desc, published_at=utcnow() - timedelta(hours=hours_ago), source_name=source)


def test_store_dedups_and_groups_stories(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    svc._loaded = True
    src = NewsSource("cnbc_top", "CNBC", "https://x")
    filtered = NewsSource("bbc_business", "BBC News", "https://y", filtered=True)
    items = [
        (item("Gold falls as dollar strengthens ahead of Fed decision", "https://a.com/1?utm_source=rss"), src),
        (item("Gold falls as dollar strengthens ahead of Fed decision", "https://a.com/1"), src),  # same URL after cleanup
        (item("Gold falls as the dollar strengthens ahead of the Fed decision", "https://b.com/2", source="Reuters"), src),  # near-identical
        (item("Gold slides as the dollar firms before the Fed meeting decision", "https://c.com/3", source="Bloomberg"), src),  # same story
        (item("Nvidia shares jump after earnings beat", "https://d.com/4"), src),
        (item("UK retail sales fall in October", "https://e.com/5", source="BBC News"), filtered),  # off-topic in a general feed
        (item("Old story", "https://f.com/6", hours_ago=24 * 9), src),  # older than 7 days
    ]
    stats = IngestStats()
    ids = svc._store(items, {"XAU": -1.5}, stats)
    assert stats.new == 3 and stats.duplicates == 2 and stats.skipped_irrelevant == 1
    with svc.db.session() as s:
        arts = {a.url: a for a in s.scalars(select(NewsArticle))}
        assert arts["https://c.com/3"].story_cluster_id == arts["https://a.com/1"].story_cluster_id
        assert arts["https://d.com/4"].story_cluster_id != arts["https://a.com/1"].story_cluster_id
        assert s.scalar(select(func.count()).select_from(ArticleAsset)) >= 3
    # Running the same items again stores nothing new.
    stats2 = IngestStats()
    svc._store(items, {}, stats2)
    assert stats2.new == 0
    # Query: the gold story counts 2 outlets and has a gold tag.
    got = svc.query(symbol="XAU", hours=24)
    assert {g["outlets"] for g in got} == {2} and all(any(t["symbol"] == "XAU" for t in g["tags"]) for g in got)
    assert len(ids) == 3


def test_importance_formula() -> None:
    assert base_importance(1.0, 5, 1.0, 3.0) == 100.0
    assert base_importance(0.5, 1, 0.0, 0.0) == 20.0

"""News ingestion: fetch feeds -> normalise -> de-duplicate -> store -> tag -> score -> fetch article text.

- Every few minutes (NEWS_INTERVAL) all RSS feeds are fetched; Google News searches every 20 min;
  Finnhub company news for the tracked stocks every 30 min (with a key).
- De-duplication: same URL (after removing tracking parameters), same normalised headline, or a nearly
  identical headline (word overlap >= 85 %) within 48 h. Headlines that share most key words (overlap >= 50 %,
  or >= 33 % with at least 4 shared words) are grouped into one "story" (story_cluster_id), so the app can
  count how many outlets cover it.
- Article text is fetched only where robots.txt allows; paywalled or blocked pages are marked
  body_fetched = False and AI breakdowns then say "Based on headline and description only."
- Importance (0-100) = source quality (40) + number of outlets on the story (25) + asset relevance (20)
  + size of the related price move right now (15). Lists rank by importance x recency.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse

import httpx
from sqlalchemy import and_, delete, func, or_, select

from app.ai.gemini_client import AIError, GeminiService
from app.db.session import Database
from app.models.tables import AppSetting, ArticleAsset, Breakdown, NewsArticle
from app.net import USER_AGENT
from app.providers import rss
from app.services import tagger
from app.services.news_sources import (
    ALL_SOURCES,
    FEEDS,
    GOOGLE_NEWS,
    GOOGLE_NEWS_MAX_ITEMS,
    NewsSource,
    is_junk,
    normalise_source,
    source_quality,
)
from app.services.textutil import (
    canonical_url,
    extract_article_text,
    extract_og_image,
    jaccard,
    title_hash,
    title_tokens,
)
from app.timeutil import utcnow

log = logging.getLogger(__name__)

MAX_AGE = timedelta(days=7)  # ignore feed items older than this
DUP_WINDOW = timedelta(hours=48)
BODY_PER_RUN = 30
DECODE_PER_RUN = 20


@dataclass
class FeedStatus:
    key: str
    name: str
    url: str
    last_ok: datetime | None = None
    last_error: datetime | None = None
    error: str | None = None
    items_last: int = 0
    new_last: int = 0
    new_total: int = 0
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class _Recent:
    id: int
    tokens: set[str]
    cluster: int
    source: str
    published: datetime | None


@dataclass
class IngestStats:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    skipped_irrelevant: int = 0
    skipped_undated: int = 0
    errors: list[str] = field(default_factory=list)


SEEN_FEEDS_KEY = "news_feeds_seen"


TOPIC_BY_SYMBOL = {"XAU": "gold", "XAG": "silver", "WTI": "oil", "BRENT": "oil", "NATGAS": "gas", "BTC": "bitcoin",
                   "US10Y": "rates", "DXY": "dollar", "USDBROAD": "dollar"}
TOPIC_BY_CLASS = {"crypto": "crypto", "metal": "gold", "energy": "oil", "stock": "stocks", "etf": "stocks", "index": "stocks",
                  "macro": "rates"}


def topic_for(tags: list[dict], official: bool = False) -> str:
    """Which topic illustration fits an article when it has no image of its own (from its strongest tag)."""
    if official:
        return "centralbank"
    if not tags:
        return "markets"
    top = max(tags, key=lambda t: t["confidence"])
    return TOPIC_BY_SYMBOL.get(top["symbol"]) or TOPIC_BY_CLASS.get(top.get("asset_class") or "", "markets")


GENERIC_IMAGE = re.compile(r"/mysterio/|logo|placeholder|default[-_]?(image|share|og)|fallback|social[-_]?share|brand[-_]?image", re.I)


def usable_image(url: str | None, generic: set[str] | None = None) -> bool:
    """False for missing images, site logos and default share images (those are not pictures of the story)."""
    if not url:
        return False
    if generic and url in generic:
        return False
    return not GENERIC_IMAGE.search(url)


def recency_factor(published: datetime | None, now: datetime) -> float:
    if published is None:
        return 0.3
    age_h = max(0.0, (now - published).total_seconds() / 3600)
    return math.exp(-age_h / 18.0)


def base_importance(quality: float, outlets: int, relevance: float, move_pct: float) -> float:
    cluster = min(1.0, max(0, outlets - 1) / 4)
    move = min(1.0, abs(move_pct) / 3.0)
    return round(40 * quality + 25 * cluster + 20 * relevance + 15 * move, 1)


class NewsService:
    def __init__(self, db: Database, http: httpx.AsyncClient, gemini: GeminiService, symbols: dict[str, str],
                 finnhub_key: str = "", move_lookup=None) -> None:  # noqa: ANN001
        self.db = db
        self.http = http
        self.gemini = gemini
        self.symbols = symbols  # symbol -> name of enabled assets
        self.finnhub_key = finnhub_key
        self.move_lookup = move_lookup or (lambda sym: 0.0)  # symbol -> current % change (real quote) or 0
        self.status: dict[str, FeedStatus] = {s.key: FeedStatus(s.key, s.name, s.url) for s in FEEDS + GOOGLE_NEWS}
        self.status["finnhub_news"] = FeedStatus("finnhub_news", "Finnhub company news", "https://finnhub.io/api/v1/company-news")
        self._recent: list[_Recent] = []
        self._lock = threading.Lock()
        self._robots: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}
        self._loaded = False
        self.last_run: datetime | None = None
        self.listeners: list = []  # callbacks(new_count)

    # ------------------------------------------------------------------ dedup cache
    def _load_recent(self) -> None:
        since = utcnow() - DUP_WINDOW
        with self.db.session() as s:
            # Remove stored non-news pages that newer filter rules reject (never ones with a breakdown).
            for a in s.scalars(select(NewsArticle).where(NewsArticle.id.not_in(select(Breakdown.article_id)))):
                if is_junk(a.title, a.source_name):
                    s.delete(a)
                elif normalise_source(a.source_name) != a.source_name:
                    a.source_name = normalise_source(a.source_name)
            s.flush()
            rows = s.execute(select(NewsArticle.id, NewsArticle.title, NewsArticle.story_cluster_id, NewsArticle.source_name,
                                    NewsArticle.published_at).where(NewsArticle.fetched_at >= since)).all()
        self._recent = [_Recent(r[0], title_tokens(r[1]), r[2] or r[0], r[3], r[4]) for r in rows]
        self._loaded = True

    # ------------------------------------------------------------------ fetching
    async def run(self, include_google: bool = False, include_finnhub: bool = False) -> IngestStats:
        if not self._loaded:
            await asyncio.to_thread(self._load_recent)
        sources = list(FEEDS) + (list(GOOGLE_NEWS) if include_google else [])
        sem = asyncio.Semaphore(6)
        stats = IngestStats()

        async def one(src: NewsSource) -> list[tuple[rss.FeedItem, NewsSource]]:
            st = self.status[src.key]
            async with sem:
                try:
                    res = await rss.fetch_feed(self.http, src.url, src.name, st.etag, st.last_modified)
                except Exception as e:  # noqa: BLE001
                    st.last_error, st.error = utcnow(), f"{type(e).__name__}: {str(e)[:120]}"
                    stats.errors.append(f"{src.key}: {st.error}")
                    return []
            st.last_ok, st.error = utcnow(), None
            st.etag, st.last_modified = res.etag, res.last_modified
            items = res.items
            if src.google_news:  # only the newest items of each search
                items = sorted(items, key=lambda i: i.published_at or utcnow(), reverse=True)[:GOOGLE_NEWS_MAX_ITEMS]
            st.items_last = len(items)
            return [(it, src) for it in items]

        batches = await asyncio.gather(*(one(s) for s in sources))
        items = [x for b in batches for x in b]
        if include_finnhub and self.finnhub_key:
            items += await self._finnhub_items()
        stats.fetched = len(items)
        moves = {sym: self.move_lookup(sym) for sym in self.symbols}
        new_ids = await asyncio.to_thread(self._store, items, moves, stats)
        if new_ids:
            await self._post_process(new_ids)
        self.last_run = utcnow()
        log.info("News: %d items fetched, %d new, %d duplicates, %d off-topic skipped, %d undated skipped, %d feed errors",
                 stats.fetched, stats.new, stats.duplicates, stats.skipped_irrelevant, stats.skipped_undated, len(stats.errors))
        for cb in self.listeners:
            try:
                cb(stats.new)
            except Exception:  # noqa: BLE001
                pass
        return stats

    async def _finnhub_items(self) -> list[tuple[rss.FeedItem, NewsSource]]:
        """Company news for tracked stocks (newest 6 per symbol). Finnhub links redirect to the publisher;
        the redirect target is read (without downloading the page) so the original URL is stored."""
        from datetime import datetime as dt
        from datetime import timezone

        st = self.status["finnhub_news"]
        src = NewsSource("finnhub_news", "Finnhub", "https://finnhub.io")
        out = []
        today = utcnow().date()
        stocks = [s for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA") if s in self.symbols]
        try:
            for sym in stocks:
                r = await self.http.get("https://finnhub.io/api/v1/company-news", params={
                    "symbol": sym, "from": str(today - timedelta(days=1)), "to": str(today), "token": self.finnhub_key})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                for n in (r.json() or [])[:6]:
                    url = n.get("url") or ""
                    if "finnhub.io/api/news" in url:
                        try:
                            rr = await self.http.get(url, follow_redirects=False, timeout=10)
                            url = rr.headers.get("location") or url
                        except httpx.HTTPError:
                            pass
                    if not url or not n.get("headline"):
                        continue
                    out.append((rss.FeedItem(title=n["headline"], url=url, description=(n.get("summary") or None),
                                             published_at=dt.fromtimestamp(int(n["datetime"]), timezone.utc) if n.get("datetime") else None,
                                             source_name=n.get("source") or "Finnhub"), src))
                await asyncio.sleep(1.2)  # stay far below 60 calls/min
            st.last_ok, st.error, st.items_last = utcnow(), None, len(out)
        except Exception as e:  # noqa: BLE001
            st.last_error, st.error = utcnow(), f"{type(e).__name__}: {str(e)[:120]}"
        return out

    # ------------------------------------------------------------------ storing (worker thread)
    def _store(self, items: list[tuple[rss.FeedItem, NewsSource]], moves: dict[str, float], stats: IngestStats) -> list[int]:
        now = utcnow()
        allowed = set(self.symbols)
        new_ids: list[int] = []
        grown_clusters: set[int] = set()
        with self._lock, self.db.session() as s:
            known_urls = set()
            # Feeds read before (remembered, or with stored articles). An item without a date in a feed's very first
            # read has an unknown age (it may be weeks old), so it is skipped instead of being shown as new.
            seen_row = s.get(AppSetting, SEEN_FEEDS_KEY)
            seen_feeds = set(seen_row.value or []) if seen_row else set()
            seen_feeds |= set(s.scalars(select(NewsArticle.source_feed).distinct()))
            for item, src in items:
                if item.published_at and now - item.published_at > MAX_AGE:
                    continue
                if item.published_at is None and src.key not in seen_feeds:
                    stats.skipped_undated += 1
                    continue
                item.source_name = normalise_source(item.source_name)
                if is_junk(item.title, item.source_name):
                    stats.skipped_irrelevant += 1
                    continue
                url = canonical_url(item.url) if not item.via_google_news else item.url
                if url in known_urls:
                    stats.duplicates += 1
                    continue
                known_urls.add(url)
                existing = s.scalars(select(NewsArticle).where(or_(NewsArticle.url == url, NewsArticle.original_url == url)).limit(1)).first()
                if existing is not None:
                    if item.image_url and not existing.image_url:  # an article seen again may now come with its image
                        existing.image_url, existing.image_source = item.image_url, "feed"
                    stats.duplicates += 1
                    continue
                h = title_hash(item.title)
                if s.scalar(select(NewsArticle.id).where(NewsArticle.canonical_hash == h,
                                                         NewsArticle.fetched_at >= now - timedelta(days=3)).limit(1)):
                    stats.duplicates += 1
                    continue
                toks = title_tokens(item.title)
                best, best_sim = None, 0.0
                for r in self._recent:
                    sim = jaccard(toks, r.tokens)
                    if sim > best_sim:
                        best, best_sim = r, sim
                if best is not None and best_sim >= 0.85:
                    stats.duplicates += 1
                    continue
                tags = tagger.keyword_tags(item.title, item.description, allowed=allowed)
                if src.filtered and not tags:
                    stats.skipped_irrelevant += 1
                    continue
                shared = len(toks & best.tokens) if best is not None else 0
                same_story = best is not None and ((best_sim >= 0.5 and shared >= 3) or (best_sim >= 0.33 and shared >= 4))
                cluster = best.cluster if same_story else None
                art = NewsArticle(
                    url=url, original_url=None if item.via_google_news else url, canonical_hash=h, title=item.title,
                    description=item.description, body_text=None, body_fetched=False, source_name=item.source_name,
                    source_feed=src.key, published_at=item.published_at, fetched_at=now, language="en",
                    importance_score=0.0, story_cluster_id=cluster, is_official=src.official or source_quality(item.source_name) == 1.0,
                    image_url=item.image_url, image_source="feed" if item.image_url else None,
                )
                s.add(art)
                s.flush()
                if art.story_cluster_id is None:
                    art.story_cluster_id = art.id
                else:
                    grown_clusters.add(art.story_cluster_id)
                for sym, conf in tags.items():
                    s.add(ArticleAsset(article_id=art.id, asset_id=self._asset_id(s, sym), tag_method="keyword", confidence=conf))
                outlets = 1 + (self._cluster_outlets(s, art.story_cluster_id, exclude=art.id, plus=item.source_name) - 1
                               if cluster else 0)
                rel = max(tags.values(), default=0.0)
                move = max((abs(moves.get(sym) or 0.0) for sym in tags), default=0.0)
                art.importance_score = base_importance(source_quality(item.source_name), outlets, rel, move)
                self._recent.append(_Recent(art.id, toks, art.story_cluster_id, item.source_name, item.published_at))
                new_ids.append(art.id)
                stats.new += 1
            # Stories that gained an outlet: re-score their articles (more outlets = more important).
            id_to_sym = {v: k for k, v in self._asset_cache.items()}
            for cid in grown_clusters:
                outlets = self._cluster_outlets(s, cid)
                for a in s.scalars(select(NewsArticle).where(NewsArticle.story_cluster_id == cid)):
                    tags = {t.asset_id: t.confidence for t in s.scalars(select(ArticleAsset).where(ArticleAsset.article_id == a.id))}
                    move = max((abs(moves.get(id_to_sym.get(aid, ""), 0.0) or 0.0) for aid in tags), default=0.0)
                    a.importance_score = base_importance(source_quality(a.source_name), outlets, max(tags.values(), default=0.0), move)
            cutoff = now - DUP_WINDOW
            self._recent = [r for r in self._recent if (r.published or now) >= cutoff]
            for src_key in {src.key for _, src in items}:
                self.status[src_key].new_last = 0
            now_seen = sorted(seen_feeds | {src.key for _, src in items})
            if seen_row is None:
                s.add(AppSetting(key=SEEN_FEEDS_KEY, value=now_seen, updated_at=now))
            elif sorted(seen_row.value or []) != now_seen:
                seen_row.value, seen_row.updated_at = now_seen, now
        if new_ids:
            with self.db.session() as s:
                for row in s.execute(select(NewsArticle.source_feed, func.count()).where(NewsArticle.id.in_(new_ids))
                                     .group_by(NewsArticle.source_feed)):
                    st = self.status.get(row[0])
                    if st:
                        st.new_last = row[1]
                        st.new_total += row[1]
        return new_ids

    _asset_cache: dict[str, int] = {}

    def _asset_id(self, s, symbol: str) -> int:  # noqa: ANN001
        if symbol not in self._asset_cache:
            from app.models.tables import Asset

            self._asset_cache[symbol] = s.scalar(select(Asset.id).where(Asset.symbol == symbol))
        return self._asset_cache[symbol]

    def _cluster_outlets(self, s, cluster_id: int, exclude: int | None = None, plus: str | None = None) -> int:  # noqa: ANN001
        q = select(NewsArticle.source_name).where(NewsArticle.story_cluster_id == cluster_id)
        if exclude is not None:
            q = q.where(NewsArticle.id != exclude)
        names = {r for r in s.scalars(q)}
        if plus:
            names.add(plus)
        return len(names)

    # ------------------------------------------------------------------ after storing: decode links, fetch text
    async def _post_process(self, new_ids: list[int]) -> None:
        rows = await asyncio.to_thread(self._load_for_post, new_ids)
        gn = [r for r in rows if r["original_url"] is None][:DECODE_PER_RUN]
        for r in gn:
            decoded = await rss.decode_google_news_url(self.http, r["url"])
            if decoded:
                r["original_url"] = canonical_url(decoded)
                await asyncio.to_thread(self._set_original, r["id"], r["original_url"])
            await asyncio.sleep(0.5)
        targets = [r for r in rows if r["original_url"] and r["tagged"]][:BODY_PER_RUN]
        sem = asyncio.Semaphore(4)

        async def body(r: dict) -> None:
            async with sem:
                await self.fetch_body(r["id"], r["original_url"])

        await asyncio.gather(*(body(r) for r in targets))
        await self.image_pass(limit=60)

    def _load_for_post(self, ids: list[int]) -> list[dict]:
        with self.db.session() as s:
            tagged = set(s.scalars(select(ArticleAsset.article_id).where(ArticleAsset.article_id.in_(ids))))
            rows = s.scalars(select(NewsArticle).where(NewsArticle.id.in_(ids)).order_by(NewsArticle.importance_score.desc())).all()
            return [{"id": a.id, "url": a.url, "original_url": a.original_url, "tagged": a.id in tagged} for a in rows]

    def _set_original(self, article_id: int, url: str) -> None:
        with self.db.session() as s:
            a = s.get(NewsArticle, article_id)
            if a and not s.scalar(select(NewsArticle.id).where(NewsArticle.original_url == url, NewsArticle.id != article_id).limit(1)):
                a.original_url = url

    async def _robots_ok(self, url: str) -> bool:
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        cached = self._robots.get(host)
        if cached is None or time.monotonic() - cached[0] > 86400:
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                r = await self.http.get(f"{host}/robots.txt", timeout=10)
                if r.status_code >= 400:
                    rp = None  # no robots.txt -> allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[host] = (time.monotonic(), rp)
            cached = self._robots[host]
        rp = cached[1]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    async def fetch_body(self, article_id: int, url: str) -> bool:
        """Fetch and store the article text (and its preview image). Returns True when usable text was found."""
        err, text, img = None, "", None
        try:
            if not await self._robots_ok(url):
                err = "robots.txt does not allow fetching this page"
            else:
                r = await self.http.get(url, timeout=20)
                if r.status_code >= 400:
                    err = f"HTTP {r.status_code} (blocked or paywalled)"
                else:
                    img = extract_og_image(r.text, str(r.url))
                    text = extract_article_text(r.text)
                    if len(text) < 500:
                        err = "Only a short text was found (likely paywall or script-only page)"
        except httpx.HTTPError as e:
            err = f"{type(e).__name__}"
        await asyncio.to_thread(self._save_body, article_id, text if not err else None, err)
        if img:
            await asyncio.to_thread(self._save_image, article_id, img)
        return err is None

    # ------------------------------------------------------------------ article images
    async def fetch_page_image(self, article_id: int, url: str) -> str | None:
        """Read only the top of the article page (up to </head>) to find the publisher's preview image."""
        img = None
        try:
            if await self._robots_ok(url):
                async with self.http.stream("GET", url, timeout=15) as r:
                    if r.status_code < 400:
                        buf = b""
                        async for chunk in r.aiter_bytes():
                            buf += chunk
                            if b"</head>" in buf or b"</HEAD>" in buf or len(buf) > 400_000:
                                break
                        img = extract_og_image(buf.decode(r.encoding or "utf-8", errors="replace"), str(r.url))
        except (httpx.HTTPError, UnicodeError):
            pass
        await asyncio.to_thread(self._save_image, article_id, img)
        return img

    def _save_image(self, article_id: int, img: str | None) -> None:
        with self.db.session() as s:
            a = s.get(NewsArticle, article_id)
            if a is None:
                return
            if img and not a.image_url:
                a.image_url, a.image_source = img, "page"
            elif not a.image_url:
                a.image_source = "none"  # looked; the page has no preview image (an illustration is shown)

    async def image_pass(self, limit: int = 80, decode_limit: int = 15) -> int:
        """Find images for recent articles that have none yet (newest and most important first)."""
        rows = await asyncio.to_thread(self._needs_image, limit)
        decoded = 0
        sem = asyncio.Semaphore(6)
        decode_sem = asyncio.Semaphore(2)  # Google News links are decoded gently, two at a time
        found = 0

        async def one(r: dict) -> None:
            nonlocal decoded, found
            url = r["original_url"]
            if url is None and "news.google.com" in r["url"]:
                if decoded >= decode_limit:
                    return
                decoded += 1
                async with decode_sem:
                    dec = await rss.decode_google_news_url(self.http, r["url"])
                if not dec:
                    await asyncio.to_thread(self._save_image, r["id"], None)
                    return
                url = canonical_url(dec)
                await asyncio.to_thread(self._set_original, r["id"], url)
            async with sem:
                if await self.fetch_page_image(r["id"], url or r["url"]):
                    found += 1

        # Google News items are decoded one after another (politely); page reads run 6 at a time.
        await asyncio.gather(*(one(r) for r in rows))
        return found

    def _needs_image(self, n: int) -> list[dict]:
        since = utcnow() - timedelta(hours=48)
        with self.db.session() as s:
            rows = s.scalars(select(NewsArticle).where(NewsArticle.fetched_at >= since, NewsArticle.image_url.is_(None),
                                                       NewsArticle.image_source.is_(None))
                             .order_by(NewsArticle.importance_score.desc(), NewsArticle.fetched_at.desc()).limit(n)).all()
            return [{"id": a.id, "url": a.url, "original_url": a.original_url} for a in rows]

    def _save_body(self, article_id: int, text: str | None, err: str | None) -> None:
        with self.db.session() as s:
            a = s.get(NewsArticle, article_id)
            if a is None:
                return
            a.body_attempted = True
            a.body_fetched = text is not None
            a.body_text = text
            a.body_error = err
            if text:
                have = set(s.scalars(select(ArticleAsset.asset_id).where(ArticleAsset.article_id == article_id)))
                if not have:  # untagged so far: try the rules on the body text
                    for sym, conf in tagger.keyword_tags("", None, text, allowed=set(self.symbols)).items():
                        s.add(ArticleAsset(article_id=article_id, asset_id=self._asset_id(s, sym), tag_method="keyword", confidence=conf))

    # ------------------------------------------------------------------ AI tagging pass
    async def ai_tag_pass(self, batch_size: int = 12) -> int:
        """Gemini tags articles the keyword rules could not place confidently. Returns the number of articles tagged."""
        if not self.gemini.configured or not self.gemini.can_run_background():
            return 0
        arts = await asyncio.to_thread(self._needs_ai_tags, batch_size)
        if not arts:
            return 0
        prompt = tagger.ai_tag_prompt(arts, self.symbols)
        try:
            res = await asyncio.to_thread(self.gemini.generate, "ai_tagging", prompt, tier="fast", schema=tagger.TagBatch)
        except AIError as e:
            log.warning("AI tagging skipped: %s", e)
            return 0
        tags = tagger.parse_ai_tags(res.parsed, {a["id"] for a in arts}, set(self.symbols))  # type: ignore[arg-type]
        await asyncio.to_thread(self._save_ai_tags, [a["id"] for a in arts], tags)
        return sum(1 for v in tags.values() if v)

    def _needs_ai_tags(self, n: int) -> list[dict]:
        since = utcnow() - timedelta(hours=36)
        with self.db.session() as s:
            confident = select(ArticleAsset.article_id).where(ArticleAsset.confidence >= 0.7)
            rows = s.scalars(select(NewsArticle).where(
                NewsArticle.fetched_at >= since, NewsArticle.ai_tagged.is_(False), NewsArticle.id.not_in(confident),
            ).order_by(NewsArticle.importance_score.desc()).limit(n)).all()
            return [{"id": a.id, "title": a.title, "description": a.description} for a in rows]

    def _save_ai_tags(self, ids: list[int], tags: dict[int, dict[str, float]]) -> None:
        with self.db.session() as s:
            for aid in ids:
                a = s.get(NewsArticle, aid)
                if a is None:
                    continue
                a.ai_tagged = True
                have = {t.asset_id: t for t in s.scalars(select(ArticleAsset).where(ArticleAsset.article_id == aid))}
                for sym, conf in tags.get(aid, {}).items():
                    asset_id = self._asset_id(s, sym)
                    if asset_id in have:
                        if conf > have[asset_id].confidence:
                            have[asset_id].confidence, have[asset_id].tag_method = conf, "ai"
                    else:
                        s.add(ArticleAsset(article_id=aid, asset_id=asset_id, tag_method="ai", confidence=conf))

    # ------------------------------------------------------------------ queries
    def query(self, *, symbol: str | None = None, asset_class: str | None = None, source: str | None = None,
              hours: float | None = 24, has_breakdown: bool | None = None, q: str | None = None, sort: str = "latest",
              limit: int = 100, min_confidence: float = 0.5, ids: list[int] | None = None,
              distinct_stories: bool = False) -> list[dict[str, Any]]:
        from app.models.tables import Asset

        now = utcnow()
        with self.db.session() as s:
            stmt = select(NewsArticle)
            conds = []
            if ids is not None:
                conds.append(NewsArticle.id.in_(ids))
            if hours:
                since = now - timedelta(hours=hours)
                conds.append(or_(NewsArticle.published_at >= since, and_(NewsArticle.published_at.is_(None), NewsArticle.fetched_at >= since)))
            if symbol or asset_class:
                sub = select(ArticleAsset.article_id).join(Asset, Asset.id == ArticleAsset.asset_id).where(ArticleAsset.confidence >= min_confidence)
                if symbol:
                    sub = sub.where(Asset.symbol == symbol.upper())
                if asset_class:
                    sub = sub.where(Asset.asset_class == asset_class)
                conds.append(NewsArticle.id.in_(sub))
            if source:
                conds.append(NewsArticle.source_name == source)
            if q:
                like = f"%{q.strip()}%"
                conds.append(or_(NewsArticle.title.ilike(like), NewsArticle.description.ilike(like)))
            bd_ids = select(Breakdown.article_id)
            if has_breakdown is True:
                conds.append(NewsArticle.id.in_(bd_ids))
            elif has_breakdown is False:
                conds.append(NewsArticle.id.not_in(bd_ids))
            if conds:
                stmt = stmt.where(and_(*conds))
            stmt = stmt.order_by(func.coalesce(NewsArticle.published_at, NewsArticle.fetched_at).desc()).limit(600 if sort == "important" else limit)
            arts = s.scalars(stmt).all()
            if not arts:
                return []
            ids_ = [a.id for a in arts]
            tag_rows = s.execute(select(ArticleAsset.article_id, Asset.symbol, ArticleAsset.confidence, ArticleAsset.tag_method,
                                        Asset.asset_class)
                                 .join(Asset, Asset.id == ArticleAsset.asset_id).where(ArticleAsset.article_id.in_(ids_))).all()
            with_bd = set(s.scalars(select(Breakdown.article_id).where(Breakdown.article_id.in_(ids_))))
            # A picture used by 3+ different articles is a site logo / default image, not a picture of this story.
            imgs = {a.image_url for a in arts if a.image_url}
            generic = {u for u, n in s.execute(select(NewsArticle.image_url, func.count()).where(NewsArticle.image_url.in_(imgs))
                                               .group_by(NewsArticle.image_url)) if n >= 3} if imgs else set()
            clusters = {a.story_cluster_id for a in arts if a.story_cluster_id}
            outlet_rows = s.execute(select(NewsArticle.story_cluster_id, func.count(func.distinct(NewsArticle.source_name)))
                                    .where(NewsArticle.story_cluster_id.in_(clusters)).group_by(NewsArticle.story_cluster_id)).all()
        outlets = dict(outlet_rows)
        tags: dict[int, list[dict]] = {}
        for aid, sym, conf, method, cls in tag_rows:
            tags.setdefault(aid, []).append({"symbol": sym, "confidence": conf, "method": method, "asset_class": cls})
        out = []
        for a in arts:
            t = sorted(tags.get(a.id, []), key=lambda x: -x["confidence"])
            rank = a.importance_score * recency_factor(a.published_at or a.fetched_at, now)
            out.append({
                "id": a.id, "title": a.title, "description": a.description, "url": a.original_url or a.url,
                "via_google_news": a.original_url is None and "news.google.com" in a.url,
                "source_name": a.source_name, "published_at": a.published_at.isoformat() if a.published_at else None,
                "fetched_at": a.fetched_at.isoformat(), "tags": t, "importance": a.importance_score, "rank": round(rank, 2),
                "has_breakdown": a.id in with_bd, "body_fetched": a.body_fetched, "outlets": outlets.get(a.story_cluster_id, 1),
                "is_official": a.is_official, "story": a.story_cluster_id,
                "image_url": a.image_url if usable_image(a.image_url, generic) else None, "image_source": a.image_source,
                "topic": topic_for(t, a.is_official),
            })
        if sort == "important":
            out.sort(key=lambda x: -x["rank"])
        if distinct_stories:  # one article per story (the best-ranked one)
            seen: set = set()
            uniq = []
            for x in out:
                if x["story"] in seen:
                    continue
                seen.add(x["story"])
                uniq.append(x)
            out = uniq
        return out[:limit]

    def counts(self, hours: int = 24) -> dict[str, int]:
        since = utcnow() - timedelta(hours=hours)
        with self.db.session() as s:
            n = s.scalar(select(func.count()).select_from(NewsArticle).where(
                func.coalesce(NewsArticle.published_at, NewsArticle.fetched_at) >= since)) or 0
            b = s.scalar(select(func.count(func.distinct(Breakdown.article_id))).join(
                NewsArticle, NewsArticle.id == Breakdown.article_id).where(
                func.coalesce(NewsArticle.published_at, NewsArticle.fetched_at) >= since)) or 0
        return {"articles": n, "breakdowns": b}

    def sources(self) -> list[str]:
        with self.db.session() as s:
            return sorted(set(s.scalars(select(NewsArticle.source_name).where(NewsArticle.fetched_at >= utcnow() - timedelta(days=7)))))

    def article(self, article_id: int) -> NewsArticle | None:
        with self.db.session() as s:
            return s.get(NewsArticle, article_id)

    def feed_status(self) -> list[dict[str, Any]]:
        return [{"key": f.key, "name": f.name, "last_ok": f.last_ok.isoformat() if f.last_ok else None,
                 "last_error": f.last_error.isoformat() if f.last_error else None, "error": f.error,
                 "items_last": f.items_last, "new_last": f.new_last, "new_total": f.new_total}
                for f in self.status.values()]

    def prune(self, days: int = 120) -> int:
        """Delete old articles without a breakdown (articles with breakdowns are kept for learning)."""
        cutoff = utcnow() - timedelta(days=days)
        with self.db.session() as s:
            r = s.execute(delete(NewsArticle).where(NewsArticle.fetched_at < cutoff,
                                                    NewsArticle.id.not_in(select(Breakdown.article_id))))
        return r.rowcount or 0


__all__ = ["NewsService", "ALL_SOURCES", "base_importance", "recency_factor"]

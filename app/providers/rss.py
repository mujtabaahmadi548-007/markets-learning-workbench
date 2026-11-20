"""RSS / Atom news feeds (no key), including Google News search feeds.

Feeds are fetched with conditional requests (ETag / Last-Modified), so unchanged feeds cost almost nothing.
Google News items link to news.google.com; `decode_google_news_url` turns such a link into the
publisher's original URL using the same request Google's own redirect page makes (unofficial; if it
fails, the Google link is kept - it still opens the original article in a browser).
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from app.services.textutil import clean_title, strip_html

GOOGLE_NEWS_HOST = "news.google.com"


@dataclass
class FeedItem:
    title: str
    url: str
    description: str | None
    published_at: datetime | None
    source_name: str
    via_google_news: bool = False
    image_url: str | None = None  # the publisher's own image for this article, when the feed provides one


@dataclass
class FeedResult:
    items: list[FeedItem]
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


def _published(e) -> datetime | None:  # noqa: ANN001
    t = e.get("published_parsed") or e.get("updated_parsed")
    if not t:
        return None
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc)


IMG_EXT = re.compile(r"\.(jpe?g|png|webp|avif)(\?|$)", re.I)


def feed_image(e) -> str | None:  # noqa: ANN001
    """The article image a feed entry carries (media:content, media:thumbnail, image enclosure, or <img> in HTML)."""
    best, best_w = None, -1
    for m in e.get("media_content") or []:
        url = m.get("url") or ""
        if url.startswith("http") and (m.get("medium") == "image" or "image" in (m.get("type") or "") or IMG_EXT.search(url)):
            try:
                w = int(m.get("width") or 0)
            except ValueError:
                w = 0
            if w > best_w:
                best, best_w = url, w
    if best:
        return best
    for m in e.get("media_thumbnail") or []:
        if (m.get("url") or "").startswith("http"):
            return m["url"]
    for enc in e.get("enclosures") or []:
        if "image" in (enc.get("type") or "") and (enc.get("href") or enc.get("url") or "").startswith("http"):
            return enc.get("href") or enc.get("url")
    html_text = (e.get("summary") or "") + "".join(c.get("value", "") for c in e.get("content") or [])
    m = re.search(r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']", html_text, re.I)
    if m and "pixel" not in m.group(1).lower() and "1x1" not in m.group(1):
        return m.group(1)
    return None


def parse_feed(content: bytes, default_source: str) -> list[FeedItem]:
    fp = feedparser.parse(content)
    items = []
    for e in fp.entries:
        link = (e.get("link") or "").strip()
        title = e.get("title") or ""
        if not link or not title:
            continue
        via_gn = urlparse(link).netloc == GOOGLE_NEWS_HOST
        source = default_source
        desc = strip_html(e.get("summary") or e.get("description") or "") or None
        if via_gn:
            src = e.get("source") or {}
            source = (src.get("title") if isinstance(src, dict) else None) or default_source
            desc = None  # Google News "descriptions" are just the headline again plus links
        title = clean_title(title, source if via_gn else None)
        if desc and len(desc) > 1200:
            desc = desc[:1200].rsplit(" ", 1)[0] + "…"
        items.append(FeedItem(title=title, url=link, description=desc, published_at=_published(e),
                              source_name=source, via_google_news=via_gn, image_url=None if via_gn else feed_image(e)))
    return items


async def fetch_feed(http: httpx.AsyncClient, url: str, default_source: str, etag: str | None = None,
                     last_modified: str | None = None) -> FeedResult:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = await http.get(url, headers=headers, timeout=20)
    if r.status_code == 304:
        return FeedResult(items=[], not_modified=True, etag=etag, last_modified=last_modified)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
    return FeedResult(items=parse_feed(r.content, default_source), etag=r.headers.get("etag"),
                      last_modified=r.headers.get("last-modified"))


async def decode_google_news_url(http: httpx.AsyncClient, url: str) -> str | None:
    """news.google.com/rss/articles/<id> -> publisher URL, or None if it cannot be decoded."""
    try:
        art_id = urlparse(url).path.rstrip("/").split("/")[-1]
        page = await http.get(f"https://news.google.com/rss/articles/{art_id}", timeout=20)
        sg = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not (sg and ts):
            return None
        inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts.group(1)},"{sg.group(1)}"]')
        r = await http.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                            data={"f.req": json.dumps([[["Fbv4je", inner]]])},
                            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=20)
        body = r.text.split("\n\n", 1)[1]
        decoded = json.loads(json.loads(body)[0][2])[1]
        return decoded if isinstance(decoded, str) and decoded.startswith("http") else None
    except Exception:  # noqa: BLE001 - unofficial; any change on Google's side just means "not decoded"
        return None

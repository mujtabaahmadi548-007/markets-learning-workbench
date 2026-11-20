"""Small text helpers for news: HTML -> text, URL/title normalisation, similarity."""
from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id", "guccounter",
                   "guce_referrer", "guce_referrer_sig", "mod", "ref", "cmpid", "ocid", "src", "fbclid", "gclid", "taid",
                   "yptr", "ncid", "soc_src", "soc_trk", "tpcc", "rss", "feed", "partner", "siteid", "__source"}
STOPWORDS = set("""a an the and or of to in on for at by with from as is are was were be been it its this that these
those after before over under amid into up down out new says say said will would could can may might than then
about against between during while just more most less least than vs via per""".split())


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def canonical_url(url: str) -> str:
    """Lower-case host, drop fragment and tracking parameters."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean_title(title: str, source_name: str | None = None) -> str:
    t = html.unescape(strip_html(title)).strip()
    if source_name:  # Google News appends " - Source"
        suffix = f" - {source_name}"
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return re.sub(r"\s+", " ", t)


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9$%][a-z0-9$%'.-]*", title.lower())
    return {w.strip(".'-") for w in words if w.strip(".'-") and w not in STOPWORDS and len(w) > 1}


def title_hash(title: str) -> str:
    norm = " ".join(sorted(title_tokens(title)))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_META_IMG = re.compile(
    r"<meta\b[^>]*?(?:property|name)\s*=\s*[\"'](?:og:image(?::url|:secure_url)?|twitter:image(?::src)?)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content\s*=\s*[\"']([^\"']+)[\"']", re.I)


def extract_og_image(page_html: str, base_url: str) -> str | None:
    """The page's own preview image (Open Graph / Twitter card), as an absolute https URL."""
    from urllib.parse import urljoin

    for tag in _META_IMG.findall(page_html[:400_000]):
        m = _CONTENT.search(tag)
        if not m:
            continue
        url = urljoin(base_url, html.unescape(m.group(1).strip()))
        low = url.lower()
        if url.startswith("http") and not any(x in low for x in ("1x1", "pixel", "spacer", "blank.", "favicon", ".svg", ".ico",
                                                                 "/mysterio/", "logo")):
            return url
    return None


class _ParagraphParser(HTMLParser):
    """Collects text of <p> elements, preferring those inside <article> / <main>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth_article = 0
        self.in_p = 0
        self.skip = 0
        self.cur: list[str] = []
        self.all_ps: list[str] = []
        self.article_ps: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style", "noscript", "nav", "footer", "aside", "form", "figcaption"):
            self.skip += 1
        elif tag in ("article", "main"):
            self.depth_article += 1
        elif tag == "p":
            self.in_p += 1
            self.cur = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "nav", "footer", "aside", "form", "figcaption"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("article", "main"):
            self.depth_article = max(0, self.depth_article - 1)
        elif tag == "p" and self.in_p:
            self.in_p -= 1
            text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
            if text:
                self.all_ps.append(text)
                if self.depth_article:
                    self.article_ps.append(text)

    def handle_data(self, data: str) -> None:
        if self.in_p and not self.skip:
            self.cur.append(data)


BOILERPLATE = re.compile(r"(cookie|subscribe|sign up|newsletter|all rights reserved|advertis|javascript|log in|"
                         r"terms of (use|service)|privacy policy|follow us|share this|read more|click here)", re.I)


def extract_article_text(page_html: str, max_chars: int = 20000) -> str:
    """Main text of an article page from its paragraphs (simple and conservative)."""
    p = _ParagraphParser()
    try:
        p.feed(page_html)
    except Exception:  # noqa: BLE001 - malformed HTML
        pass
    paras = p.article_ps if sum(len(x) for x in p.article_ps) > 400 else p.all_ps
    seen, out = set(), []
    for para in paras:
        if len(para) < 40 or BOILERPLATE.search(para) and len(para) < 200 or para in seen:
            continue
        seen.add(para)
        out.append(para)
    return "\n\n".join(out)[:max_chars]

r"""Build the website version (GitHub Pages) of the Markets Learning Workbench.

GitHub runs this about every 10 minutes (.github/workflows/site.yml). It starts the real app engine for
about a minute, collects fresh prices and news, does a small, budgeted amount of AI work, then writes
every page and data file into a folder that GitHub Pages publishes.

    python build_site.py --out _site --base /markets-learning-workbench          (what GitHub runs)
    python build_site.py --out C:\temp\site --base /markets-learning-workbench --no-ai   (local test)

Nothing here invents data: the pages are the app's own pages, rendered from the stored real data.
Keys come from environment variables (GitHub secrets) and are never written into the site.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="_site", help="output folder (deleted and rebuilt)")
    p.add_argument("--base", default=os.environ.get("MLW_SITE_BASE", ""), help="site path, e.g. /markets-learning-workbench")
    p.add_argument("--no-ai", action="store_true", help="do not call Gemini")
    p.add_argument("--no-news", action="store_true", help="skip fetching news (use stored articles)")
    p.add_argument("--ai-tasks", type=int, default=int(os.environ.get("SITE_AI_TASKS_PER_RUN", "2")),
                   help="maximum AI tasks per build (each is 1-3 Gemini calls)")
    p.add_argument("--warmup", type=int, default=45, help="seconds to let live prices arrive")
    return p.parse_args()


ARGS = parse_args()
BASE = ARGS.base.rstrip("/")
# These must be set before the app is imported (templates read them at import time).
os.environ["MLW_SITE_BUILD"] = "1"
os.environ["MLW_SITE_BASE"] = BASE
os.environ["MLW_BUILD"] = "1"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.ai.gemini_client import AIError  # noqa: E402
from app.ai.pacing import briefing_date_in_quota_day, paced_allowance  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.tables import AppSetting, Breakdown, NewsArticle, QuizQuestion  # noqa: E402
from app.services.candles import RANGES  # noqa: E402
from app.timeutil import JAKARTA, utcnow  # noqa: E402

LOG: list[str] = []
ENTRIES: list[dict] = []  # the same lines with full UTC times, for the website's Health page


def say(msg: str) -> None:
    now = datetime.now(timezone.utc)
    line = f"[{now.strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    ENTRIES.append({"t": now.isoformat(timespec="seconds"), "msg": msg})
    print(line, flush=True)


# ---------------------------------------------------------------- state kept between builds (in the database)
def get_state(st) -> dict:  # noqa: ANN001
    with st.db.session() as s:
        row = s.get(AppSetting, "site_state")
        return dict(row.value) if row and isinstance(row.value, dict) else {}


def set_state(st, data: dict) -> None:  # noqa: ANN001
    with st.db.session() as s:
        row = s.get(AppSetting, "site_state")
        if row is None:
            s.add(AppSetting(key="site_state", value=data, updated_at=utcnow()))
        else:
            row.value, row.updated_at = data, utcnow()


def older_than(state: dict, key: str, minutes: float) -> bool:
    t = state.get(key)
    return t is None or utcnow() - datetime.fromisoformat(t) > timedelta(minutes=minutes)


def mark(state: dict, key: str) -> None:
    state[key] = utcnow().isoformat()


# ---------------------------------------------------------------- steps
def wait_for_prices(st, seconds: int) -> None:  # noqa: ANN001
    deadline = time.time() + seconds
    while time.time() < deadline:
        views = st.feeds.views()
        waiting = [v["symbol"] for v in views if v["status"] == "CONNECTING"]
        if not waiting and time.time() > deadline - seconds + 15:
            break
        time.sleep(3)
    views = st.feeds.views()
    by = {}
    for v in views:
        by[v["status"]] = by.get(v["status"], 0) + 1
    say(f"prices: {by}")


def collect_news(st, portal, state: dict) -> None:  # noqa: ANN001
    google = older_than(state, "google_news", 19)
    finnhub = older_than(state, "finnhub_news", 29)
    stats = portal.call(st.news.run, google, finnhub)
    if google:
        mark(state, "google_news")
    if finnhub:
        mark(state, "finnhub_news")
    say(f"news: {stats.fetched} fetched, {stats.new} new, {stats.duplicates} duplicates (google={google})")
    found = portal.call(st.news.image_pass, 120)
    say(f"images: {found} found")
    if older_than(state, "calendar", 12 * 60) or "calendar_status" not in state:
        n = portal.call(st.calendar.refresh)
        mark(state, "calendar")
        state["calendar_status"] = dict(st.calendar.status)
        say(f"calendar: {n} events")
    else:
        # The calendar is fetched every 12 hours, not every build: show the last fetch's result on the Health page.
        st.calendar.status.update(state["calendar_status"])


def ai_budget_left(st) -> int:  # noqa: ANN001
    return st.settings.gemini_daily_call_limit - st.gemini.calls_today()


# Gemini calls kept free for the 7:00 AM Jakarta briefing (it also writes breakdowns for the top 3 stories;
# a briefing used about 10 calls in Nov 2026). The rest of the daily limit is spread over the whole quota day,
# see app/ai/pacing.py.
BRIEFING_RESERVE = 20


def ai_pace(st) -> tuple[int, int, bool]:  # noqa: ANN001
    """(calls used today, calls allowed by now, today's briefing already made)."""
    latest = st.briefing.get()
    briefing_done = latest is not None and latest["date"] == briefing_date_in_quota_day()
    allowed = paced_allowance(st.settings.gemini_daily_call_limit, BRIEFING_RESERVE, briefing_done)
    return st.gemini.calls_today(), allowed, briefing_done


def ai_work(st, portal, state: dict, max_tasks: int) -> None:  # noqa: ANN001
    """A few AI tasks per build, most useful first, inside the daily call limit."""
    if not st.gemini.configured or not (st.settings.model_fast or st.settings.model_smart):
        say("AI: no GEMINI_API_KEY / model set - skipped")
        return
    if ai_budget_left(st) < 10:
        say(f"AI: daily limit nearly used ({st.gemini.calls_today()}/{st.settings.gemini_daily_call_limit}) - skipped")
        return
    now_jkt = utcnow().astimezone(JAKARTA)
    done = 0

    def run(label: str, fn, paced: bool = True) -> bool:  # noqa: ANN001
        nonlocal done
        if done >= max_tasks or ai_budget_left(st) < 6:
            return False
        if paced:
            used, allowed, _ = ai_pace(st)
            if used >= allowed:
                return False
        try:
            result = fn()
            say(f"AI: {label} -> {result}")
        except AIError as e:
            say(f"AI: {label} failed: {e}")
        except Exception as e:  # noqa: BLE001
            say(f"AI: {label} error: {type(e).__name__}: {e}")
        done += 1
        return True

    # 1) Daily briefing at 07:00 Jakarta (writes breakdowns for the top 3 stories too). Not paced: it uses the
    #    calls kept for it.
    latest = st.briefing.get()
    if now_jkt.hour >= 7 and (latest is None or latest["date"] != now_jkt.date().isoformat()):
        run("daily briefing", lambda: (portal.call(st.briefing.generate, True), "made")[1], paced=False)
    # 2) Tag unclear articles.
    if older_than(state, "ai_tagging", 20) and run("tagging", lambda: portal.call(st.news.ai_tag_pass)):
        mark(state, "ai_tagging")
    # 3) Breakdown for the most important story without one.
    if older_than(state, "auto_breakdown", 15) and run("breakdown", lambda: portal.call(st.breakdowns.auto_run, 1)):
        mark(state, "auto_breakdown")
    # 4) 'Why did it move?' for the biggest movers (each at most every 3 hours).
    movers = sorted([v for v in st.feeds.views() if v["change_pct"] is not None and v["kind"] != "daily"
                     and v["status"] in ("LIVE", "DELAYED", "MARKET_CLOSED")], key=lambda v: -abs(v["change_pct"]))[:3]
    why_state = state.setdefault("why", {})
    for v in movers:
        last = why_state.get(v["symbol"])
        if last is None or utcnow() - datetime.fromisoformat(last) > timedelta(hours=3):
            if run(f"why {v['symbol']}", lambda v=v: (portal.call(st.explainer.explain, v["symbol"], True), "made")[1]):
                why_state[v["symbol"]] = utcnow().isoformat()
            break
    # 5) Quiz questions: newest broken-down article without questions, then lessons without questions.
    with st.db.session() as s:
        have = {(t, i) for t, i in s.execute(select(QuizQuestion.source_type, QuizQuestion.source_id).distinct())}
        art = s.execute(select(NewsArticle.id).join(Breakdown, Breakdown.article_id == NewsArticle.id)
                        .where(NewsArticle.body_fetched.is_(True)).order_by(Breakdown.created_at.desc()).limit(20)).scalars().all()
    art_todo = next((a for a in art if ("article", a) not in have), None)
    lessons = [l["id"] for p in st.learning.paths() for l in p["lessons"]]
    lesson_todo = next((l for l in lessons if ("lesson", l) not in have), None)
    if art_todo is not None and older_than(state, "quiz_article", 45):
        if run(f"quiz from article {art_todo}", lambda: st.learning.generate_quiz("article", art_todo, 3)):
            mark(state, "quiz_article")
    elif lesson_todo is not None:
        run(f"quiz from lesson {lesson_todo}", lambda: st.learning.generate_quiz("lesson", lesson_todo, 3))
    # 6) Glossary: explain one more concept (lesson concepts first).
    concepts = [c for c in st.learning.concepts() if not c["explained"]]
    if concepts:
        run(f"explain concept {concepts[0]['slug']}", lambda: (st.learning.explain_concept(concepts[0]["slug"]), "done")[1])
    used, allowed, briefing_done = ai_pace(st)
    limit = st.settings.gemini_daily_call_limit
    kept = "" if briefing_done else f"; {min(BRIEFING_RESERVE, limit)} kept for the 7:00 AM briefing"
    say(f"AI calls today: {used}/{limit} (spread over the day: {min(allowed, limit)} allowed by now{kept})")


# ---------------------------------------------------------------- export
HREF = re.compile(r'((?:href|src|action|data-fallback)=")/(?!/)')


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def page(client, out: Path, url: str, target: str) -> bool:  # noqa: ANN001
    r = client.get(url)
    if r.status_code != 200 and target != "404.html":
        say(f"page {url}: HTTP {r.status_code}")
        return False
    html = HREF.sub(lambda m: m.group(1) + (BASE + "/" if BASE else "/"), r.text) if BASE else r.text
    write(out / target, html)
    return True


def dump(out: Path, rel: str, data) -> None:  # noqa: ANN001
    write(out / "data" / rel, json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))


def export(client, st, portal, out: Path) -> None:  # noqa: ANN001
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(ROOT / "static", out / "static")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    built = utcnow().isoformat()

    # Data files
    portal.call(st.candles.refresh_sparklines, list(st.feeds.assets.values()))
    dump(out, "quotes.json", {"built_at": built, "quotes": st.feeds.views()})
    dump(out, "sparklines.json", client.get("/api/sparklines").json())
    articles = st.news.query(hours=72, limit=5000)
    for a in articles:
        if a.get("description") and len(a["description"]) > 320:
            a["description"] = a["description"][:320].rsplit(" ", 1)[0] + "…"
    assets = sorted(st.feeds.assets.values(), key=lambda a: (a.sort_order, a.id))
    write_news(out, articles, [a.symbol for a in assets], built)
    for a in assets:
        d = client.get(f"/api/assets/{a.symbol}").json()
        d.pop("server_time", None)
        dump(out, f"assets/{a.symbol}.json", d)
        dump(out, f"why/{a.symbol}.json", client.get(f"/api/assets/{a.symbol}/why").json())
        for rng in RANGES:
            dump(out, f"candles/{a.symbol}/{rng}.json", client.get(f"/api/candles/{a.symbol}?range={rng}").json())
    say(f"data: {len(articles)} articles, {len(assets)} assets")

    paths = st.learning.paths()
    lessons = [{"id": l["id"], "title": l["title"], "path": p["key"], "path_title": p["title"]} for p in paths for l in p["lessons"]]
    concepts = st.learning.concepts()
    dump(out, "learning.json", {"built_at": built, "lessons": lessons,
                                "concepts": {c["slug"]: c["name"] for c in concepts}, "questions": st.learning.pool()})

    # Pages
    n = 0
    fixed = [("/", "index.html"), ("/news", "news/index.html"), ("/learn", "learn/index.html"), ("/glossary", "glossary/index.html"),
             ("/quiz", "quiz/index.html"), ("/briefing", "briefing/index.html"), ("/alerts", "alerts/index.html"),
             ("/demo", "demo/index.html"), ("/settings", "settings/index.html"), ("/health", "health/index.html"), ("/__missing__", "404.html")]
    for url, target in fixed:
        n += page(client, out, url, target)
    for a in assets:
        n += page(client, out, f"/asset/{a.symbol}", f"asset/{a.symbol}/index.html")
    for l in lessons:
        n += page(client, out, f"/learn/{l['id']}", f"learn/{l['id']}/index.html")
    for c in concepts:
        n += page(client, out, f"/glossary/{c['slug']}", f"glossary/{c['slug']}/index.html")
    for day in st.briefing.dates():
        n += page(client, out, f"/briefing?day={day}", f"briefing/{day}/index.html")
    with st.db.session() as s:
        bd_ids = set(s.scalars(select(Breakdown.article_id)))
    art_ids = [a["id"] for a in articles] + sorted(bd_ids - {a["id"] for a in articles})
    for aid in art_ids:
        n += page(client, out, f"/news/{aid}", f"news/{aid}/index.html")
    n += export_linked_articles(client, out, set(art_ids))
    # Price moves since publication: articles with breakdowns + the most important recent ones. The index lets
    # article pages know whether a file exists, instead of asking for it and getting a 404.
    top = [a["id"] for a in sorted(articles, key=lambda x: -x["rank"])[:60]]
    with_moves = []
    for aid in sorted(set(top) | bd_ids):
        r = client.get(f"/api/articles/{aid}/moves")
        if r.status_code == 200:
            dump(out, f"moves/{aid}.json", r.json())
            with_moves.append(aid)
    dump(out, "moves/index.json", with_moves)
    say(f"pages: {n} written")
    check_links(out)
    dump(out, "meta.json", {"built_at": built, "log": LOG[-40:], "entries": ENTRIES[-40:]})


# ---------------------------------------------------------------- news files
# Phones should not download three days of news (about 0.6 MB compressed) to show six headlines. The browser
# reads the smallest file that holds what a page needs (static/js/static-site.js):
#   news-top.json        the highest-ranked articles of the last 24 hours (dashboard "Top stories")
#   news-asset/SYM.json  the highest-ranked articles tagged with one asset, last 72 hours (asset pages)
#   news-24h.json        every article of the last 24 hours (News page default)
#   news.json            every article of the last 72 hours (longer time windows)
# Ranking is importance x exp(-age/18 h), the same formula as the browser. Time passing multiplies every score
# by the same factor, so the order - and therefore a ranked excerpt's top items - stays the same until the next build.
NEWS_TOP = 80
NEWS_PER_ASSET = 40
NEWS_FIELDS = ("id", "title", "description", "url", "source_name", "published_at", "importance", "tags",
               "has_breakdown", "outlets", "is_official", "story", "image_url", "topic")


def slim_article(a: dict) -> dict:
    """Only the fields the website's pages read (renderArticle in common.js and the news filters)."""
    s = {k: a.get(k) for k in NEWS_FIELDS}
    if not s["published_at"]:
        s["fetched_at"] = a.get("fetched_at")
    # Pages only ever show or filter on tags with confidence >= 0.5.
    s["tags"] = [t for t in (a.get("tags") or []) if (t.get("confidence") or 0) >= 0.5]
    # Empty values cost bytes and mean the same as a missing key to the page code. ("story" is used as a key and
    # "importance" in arithmetic, so both always stay; note 0 == False in Python.)
    return {k: v for k, v in s.items() if k in ("story", "importance") or v not in (None, False, "", [])}


def article_age_hours(a: dict, now: datetime) -> float:
    t = a.get("published_at") or a.get("fetched_at")
    if not t:
        return 0.0
    dt = t if isinstance(t, datetime) else datetime.fromisoformat(str(t))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600


def write_news(out: Path, articles: list[dict], symbols: list[str], built: str) -> None:
    now = utcnow()
    rows = [(slim_article(a), article_age_hours(a, now)) for a in articles]
    sources = sorted({a["source_name"] for a in articles if a.get("source_name")})
    day = [s for s, age in rows if age <= 24]

    def by_rank(items: list[tuple[dict, float]]) -> list[dict]:
        return [s for s, _ in sorted(items, key=lambda r: -(r[0]["importance"] or 0) * math.exp(-max(0.0, r[1]) / 18))]

    dump(out, "news.json", {"built_at": built, "articles": [s for s, _ in rows], "sources": sources})
    dump(out, "news-24h.json", {"built_at": built, "articles": day, "sources": sources})
    top = by_rank([r for r in rows if r[1] <= 24])
    dump(out, "news-top.json", {"built_at": built, "articles": top[:NEWS_TOP], "complete": len(top) <= NEWS_TOP})
    for sym in symbols:
        mine = by_rank([r for r in rows if any(t["symbol"] == sym for t in r[0].get("tags", []))])
        dump(out, f"news-asset/{sym}.json", {"built_at": built, "articles": mine[:NEWS_PER_ASSET],
                                              "complete": len(mine) <= NEWS_PER_ASSET})


NEWS_LINK = re.compile(r'href="' + re.escape(BASE) + r'/news/(\d+)/?["#?]')
INTERNAL_LINK = re.compile(r'(?:href|src|data-fallback)="(' + re.escape(BASE) + r'/(?!/)[^"#?]*)')


def export_linked_articles(client, out: Path, have: set[int]) -> int:  # noqa: ANN001
    """Write the pages of older articles that other pages link to (for example the real news examples on
    glossary pages), so no link on the website leads to "Not found"."""
    n, scan = 0, list(out.rglob("*.html"))
    for _ in range(5):  # linked pages can link to more articles; a few rounds cover that
        want: set[int] = set()
        for f in scan:
            want.update(int(x) for x in NEWS_LINK.findall(f.read_text(encoding="utf-8")))
        missing = sorted(want - have)
        if not missing:
            break
        have.update(missing)
        scan = []
        for aid in missing:
            target = f"news/{aid}/index.html"
            if page(client, out, f"/news/{aid}", target):
                n += 1
                scan.append(out / target)
    if n:
        say(f"pages: {n} older articles that other pages link to")
    return n


def check_links(out: Path) -> None:
    """Check every internal link and file reference in the written pages; anything missing goes into the build
    log (and so onto the website's Health page) instead of silently showing "Not found" to a reader."""
    prefix = BASE + "/"
    missing: dict[str, str] = {}
    for f in out.rglob("*.html"):
        if f.name == "404.html":
            continue
        for url in INTERNAL_LINK.findall(f.read_text(encoding="utf-8")):
            if "${" in url:  # a link built by a page script at view time, not a fixed link
                continue
            rel = unquote(url[len(prefix):] if url.startswith(prefix) else url.lstrip("/"))
            target = out / rel
            if rel in missing or target.is_file() or (target / "index.html").is_file():
                continue
            missing[rel] = f.relative_to(out).as_posix()
    if missing:
        sample = "; ".join(f"/{k} (linked on /{v})" for k, v in list(missing.items())[:5])
        say(f"links: {len(missing)} broken - {sample}")
    else:
        say("links: every internal link works")


def main() -> int:
    t0 = time.time()
    app = create_app()
    with TestClient(app) as client:
        st = app.state.mlw
        portal = client.portal
        state = get_state(st)
        wait_for_prices(st, ARGS.warmup)
        if not ARGS.no_news:
            collect_news(st, portal, state)
        if not ARGS.no_ai:
            ai_work(st, portal, state, ARGS.ai_tasks)
        set_state(st, state)
        export(client, st, portal, Path(ARGS.out).resolve())
    with app.state.mlw.db.session() as s:
        n_articles = s.scalar(select(func.count()).select_from(NewsArticle))
    say(f"done in {time.time() - t0:.0f}s ({n_articles} articles stored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

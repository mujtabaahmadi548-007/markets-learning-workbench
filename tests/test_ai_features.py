"""Breakdowns, 'Why did it move?' and quizzes, end to end against the local fake Gemini (no real key)."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.ai.gemini_client import GeminiService
from app.config import load_settings
from app.db.migrations import init_db
from app.db.session import Database
from app.models.tables import ArticleAsset, Asset, Concept, NewsArticle, ReviewSchedule
from app.services.assets import load_assets, seed_assets
from app.services.breakdowns import BreakdownService
from app.services.explainer import NO_DRIVER, MoveExplainer
from app.services.feeds import FeedManager
from app.services.learning import LearningService, seed_learning
from app.services.news import NewsService
from app.timeutil import utcnow

BODY = ("Gold prices fell 1.2% to $3,640 an ounce on Thursday as the dollar strengthened after strong US jobs data. "
        "Traders reduced bets on a Federal Reserve rate cut in December. " * 6)


def setup(tmp_path: Path, monkeypatch, fake):  # noqa: ANN001, ANN201
    monkeypatch.setenv("GEMINI_API_KEY", "FAKEKEY-tests-only-1234")
    monkeypatch.setenv("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_MODEL_SMART", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_BASE_URL", fake.base_url)
    settings = load_settings(env_file=None)
    db = Database(tmp_path / "ai.db")
    init_db(db)
    seed_assets(db)
    seed_learning(db)
    gemini = GeminiService(settings, db)
    assets = load_assets(db)
    symbols = {a.symbol: a.name for a in assets}
    news = NewsService(db, None, gemini, symbols)  # type: ignore[arg-type]
    with db.session() as s:
        a = NewsArticle(url="https://example.com/gold", original_url="https://example.com/gold", canonical_hash="h1",
                        title="Gold falls as dollar firms after jobs data", description="Gold fell on Thursday.",
                        body_text=BODY, body_fetched=True, body_attempted=True, source_name="CNBC", source_feed="cnbc_top",
                        published_at=utcnow() - timedelta(hours=2), fetched_at=utcnow(), importance_score=60, story_cluster_id=None)
        s.add(a)
        s.flush()
        a.story_cluster_id = a.id
        gold = s.scalar(select(Asset.id).where(Asset.symbol == "XAU"))
        s.add(ArticleAsset(article_id=a.id, asset_id=gold, tag_method="keyword", confidence=0.9))
        art_id = a.id
    return db, gemini, news, symbols, assets, art_id


def breakdown_json(what: str) -> dict:
    return {"what_happened": what, "why_it_matters": "A stronger dollar can weigh on gold.",
            "affected_assets": [{"symbol": "XAU", "direction_suggested_by_article": "down", "mechanism": "strong jobs -> fewer rate cuts -> stronger dollar -> gold down"},
                                {"symbol": "DOGE", "direction_suggested_by_article": "up", "mechanism": "not tracked"}],
            "concepts": [{"slug": "Rate Expectations", "name": "Rate expectations", "one_line": "What markets think the Fed will do."}],
            "facts_vs_opinion": {"facts": ["Gold fell 1.2%."], "opinions_or_speculation": []},
            "beginner_explanation": "When the dollar gets stronger, gold often gets cheaper.", "basis": "headline_and_description_only"}


def test_breakdown_valid_numbers(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, symbols, _, art_id = setup(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.replies = [breakdown_json("Gold fell 1.2% to about $3,640 after jobs data.")]
    svc = BreakdownService(db, gemini, news, None, symbols)
    b = svc.create(art_id)
    assert b["validation_passed"] is True
    c = b["content"]
    assert c["basis"] == "full_article"  # set from what was really fetched, not from the model
    assert [x["symbol"] for x in c["affected_assets"]] == ["XAU"]  # untracked symbol dropped
    assert c["concepts"][0]["slug"] == "rate-expectations"
    assert "FULL ARTICLE TEXT" in fake_gemini.prompts[-1]
    with db.session() as s:
        assert s.scalar(select(Concept.id).where(Concept.slug == "rate-expectations"))


def test_breakdown_invented_number_retry_then_strip(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, symbols, _, art_id = setup(tmp_path, monkeypatch, fake_gemini)
    bad = breakdown_json("Gold fell 1.2%. It was the worst day in 14 years.")
    fake_gemini.replies = [bad, bad]
    b = BreakdownService(db, gemini, news, None, symbols).create(art_id)
    assert b["validation_passed"] is False and "14" in b["validation_notes"]
    assert b["content"]["what_happened"] == "Gold fell 1.2%."
    assert "NOT appear in the data" in fake_gemini.prompts[-1]  # the stricter retry prompt was used


def test_breakdown_retry_succeeds(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, symbols, _, art_id = setup(tmp_path, monkeypatch, fake_gemini)
    fake_gemini.replies = [breakdown_json("Gold fell 7%."), breakdown_json("Gold fell 1.2%.")]
    b = BreakdownService(db, gemini, news, None, symbols).create(art_id)
    assert b["validation_passed"] is True and "retry" in b["validation_notes"]


def _feeds_with_gold_change(db: Database, assets) -> FeedManager:  # noqa: ANN001
    feeds = FeedManager(db, {}, assets)
    gold = next(a for a in assets if a.symbol == "XAU")
    lq = feeds.quotes[gold.id]
    lq.price, lq.change, lq.change_pct, lq.prev_close = 3640.0, -44.2, -1.2, 3684.2
    lq.change_basis, lq.source_timestamp, lq.last_success_at, lq.kind = "24h_recorded", utcnow(), utcnow(), "stream"
    lq.provider, lq.provider_label, lq.provider_symbol = "finnhub", "Finnhub", "OANDA:XAU_USD"
    return feeds


def test_why_move_cites_only_real_articles(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, _, assets, art_id = setup(tmp_path, monkeypatch, fake_gemini)
    feeds = _feeds_with_gold_change(db, assets)
    fake_gemini.replies = [{"summary": "Gold fell 1.2% as the dollar firmed.", "unexplained": False, "note": "Timing is not proof.",
                            "likely_drivers": [{"driver": "Strong jobs data lifted the dollar", "evidence_article_ids": [art_id, 999999], "confidence": "medium"},
                                               {"driver": "Invented driver", "evidence_article_ids": [424242], "confidence": "high"}]}]
    out = asyncio.run(MoveExplainer(db, gemini, news, feeds).explain("XAU", force=True))
    drivers = out["content"]["likely_drivers"]
    assert len(drivers) == 1 and drivers[0]["evidence_article_ids"] == [art_id]
    assert out["content"]["unexplained"] is False and out["article_ids"] == [art_id]
    assert "-1.20%" in fake_gemini.prompts[-1] or "-1.20" in fake_gemini.prompts[-1]


def test_why_move_without_evidence_says_no_driver(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, _, assets, _ = setup(tmp_path, monkeypatch, fake_gemini)
    feeds = _feeds_with_gold_change(db, assets)
    fake_gemini.replies = [{"summary": "Gold moved.", "unexplained": False, "note": "",
                            "likely_drivers": [{"driver": "Something", "evidence_article_ids": [123456], "confidence": "low"}]}]
    out = asyncio.run(MoveExplainer(db, gemini, news, feeds).explain("XAU", force=True))
    assert out["content"]["unexplained"] is True and out["content"]["summary"].startswith(NO_DRIVER)


def test_why_move_no_news_no_ai_call(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, _, assets, _ = setup(tmp_path, monkeypatch, fake_gemini)
    feeds = _feeds_with_gold_change(db, assets)
    btc = next(a for a in assets if a.symbol == "BTC")
    lq = feeds.quotes[btc.id]
    lq.price, lq.change, lq.change_pct, lq.change_basis, lq.source_timestamp = 61000.0, -900.0, -1.4, "24h", utcnow()
    n = len(fake_gemini.requests)
    out = asyncio.run(MoveExplainer(db, gemini, news, feeds).explain("BTC", force=True))
    assert out["content"]["summary"] == NO_DRIVER and len(fake_gemini.requests) == n


def test_quiz_checker_and_spaced_repetition(tmp_path: Path, monkeypatch, fake_gemini) -> None:
    db, gemini, news, _, _, art_id = setup(tmp_path, monkeypatch, fake_gemini)
    q = lambda text, idx: {"question": text, "options": ["Rose", "Fell", "Unchanged", "Not reported"], "correct_index": idx,  # noqa: E731
                           "explanation": "The article says gold fell.", "option_explanations": ["a", "b", "c", "d"],
                           "difficulty": "easy", "concept_slugs": []}
    fake_gemini.replies = [
        {"questions": [q("What did gold do?", 1), q("Bad key question?", 0)]},
        {"verdicts": [{"index": 0, "answer_supported_by_source": True, "correct_index_according_to_source": 1},
                      {"index": 1, "answer_supported_by_source": True, "correct_index_according_to_source": 1}]},
    ]
    learning = LearningService(db, gemini, news)
    res = learning.generate_quiz("article", art_id, 2)
    assert res == {"kept": 1, "discarded": 1, "validation_notes": ""}
    qs = learning.next_questions("today")
    assert len(qs) == 1 and qs[0]["source"]["id"] == art_id
    wrong = learning.answer(qs[0]["id"], 0)
    assert wrong["correct"] is False and wrong["correct_index"] == 1
    assert learning.next_questions("review") == []  # due again in 10 minutes, not now
    with db.session() as s:
        row = s.get(ReviewSchedule, qs[0]["id"])
        row.next_review_at = utcnow() - timedelta(minutes=1)
    assert [x["id"] for x in learning.next_questions("review")] == [qs[0]["id"]]
    right = learning.answer(qs[0]["id"], 1)
    assert right["correct"] is True
    assert learning.stats()["answered"] == 2

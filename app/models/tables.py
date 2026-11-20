"""SQLAlchemy models: the full schema from the project spec (section 7), plus a few support tables.

Differences from the spec (explained in README "Database"):
- lessons.order is named `sort_order` (ORDER is an SQL reserved word).
- Extra columns for data honesty: quotes_latest.delay_seconds / quote_currency / is_proxy /
  is_unofficial / error_message / prev_close / last_success_at.
- news_articles.story_cluster_id (how many outlets cover the same story) and original_url
  (the real publisher URL when the feed link is an aggregator redirect, e.g. Google News).
- quiz_questions.option_explanations (one explanation per answer) and checker_passed.
- Extra tables: calendar_events, app_settings, job_runs, schema_version.
All datetimes are timezone-aware UTC (see app.db.types.UTCDateTime).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCDateTime
from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- market data
class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # crypto | metal | energy | stock | etf | index | macro
    asset_class: Mapped[str] = mapped_column(String(16), index=True)
    # Ordered fallback chains for prices and for charts, e.g.
    # {"quote": [{"provider": "binance", "symbol": "BTCUSDT", "note": "..."}, {"provider": "gateio", ...}],
    #  "candles": [...], "unavailable_reason": null}
    provider_symbols: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decimals: Mapped[int] = mapped_column(Integer, default=2)  # price display precision (added in schema v2)
    # Key of a schedule in the market-hours service, e.g. "crypto_24_7", "us_equity", "cme_globex", "fx_metals", "daily_series"
    market_schedule: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class QuoteLatest(Base):
    __tablename__ = "quotes_latest"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float)
    change: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    change_basis: Mapped[str | None] = mapped_column(String(16))  # 24h | prev_close | prev_obs | 24h_recorded (v3)
    prev_close: Mapped[float | None] = mapped_column(Float)
    day_high: Mapped[float | None] = mapped_column(Float)
    day_low: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(48))  # "provider:symbol"
    # LIVE | DELAYED | MARKET_CLOSED | STALE | ERROR
    status: Mapped[str] = mapped_column(String(16), default="ERROR")
    delay_seconds: Mapped[int | None] = mapped_column(Integer)  # provider's documented/measured delay
    quote_currency: Mapped[str | None] = mapped_column(String(8))  # USD, USDT, ...
    is_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    is_unofficial: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    source_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (Index("ix_snap_asset_ts", "asset_id", "source_timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(48))
    source_timestamp: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "ts", "source", name="uq_candle"),
        Index("ix_candle_asset_tf_ts", "asset_id", "timeframe", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(4))  # 1m | 5m | 1h | 1d
    ts: Mapped[datetime] = mapped_column(UTCDateTime)  # candle open time
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(48))


class ProviderStatus(Base):
    __tablename__ = "provider_status"

    provider: Mapped[str] = mapped_column(String(48), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # OK | DEGRADED | DOWN | UNKNOWN | DISABLED
    last_success: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- news
class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    original_url: Mapped[str | None] = mapped_column(String(2048))
    canonical_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    source_name: Mapped[str] = mapped_column(String(128))
    source_feed: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    language: Mapped[str | None] = mapped_column(String(8))
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    story_cluster_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # schema v4
    ai_tagged: Mapped[bool] = mapped_column(Boolean, default=False)
    body_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    body_error: Mapped[str | None] = mapped_column(Text)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)  # central bank / government source
    # schema v5: the publisher's own image (from the feed, or the page's preview image).
    # image_source: feed | page | none (looked, nothing found) | NULL (not looked yet)
    image_url: Mapped[str | None] = mapped_column(String(2048))
    image_source: Mapped[str | None] = mapped_column(String(8))


class ArticleAsset(Base):
    __tablename__ = "article_assets"

    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    tag_method: Mapped[str] = mapped_column(String(16))  # keyword | ai
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class Breakdown(Base):
    __tablename__ = "breakdowns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), index=True)
    model_used: Mapped[str] = mapped_column(String(64))
    json_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MoveExplanation(Base):
    __tablename__ = "move_explanations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    price_change_pct: Mapped[float | None] = mapped_column(Float)
    json_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    article_ids_used: Mapped[list[int]] = mapped_column(JSON, default=list)
    model_used: Mapped[str | None] = mapped_column(String(64))
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)  # source + its own id/hash
    source: Mapped[str] = mapped_column(String(48))  # bls | fomc | forexfactory ...
    title: Mapped[str] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(8))
    event_time: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    importance: Mapped[str | None] = mapped_column(String(16))
    url: Mapped[str | None] = mapped_column(String(2048))
    is_unofficial: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --------------------------------------------------------------------------- learning
class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(48))
    short_definition: Mapped[str | None] = mapped_column(Text)
    long_explanation: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    related_concepts: Mapped[list[str]] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(16), default="ai")  # ai | edited
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ConceptProgress(Base):
    __tablename__ = "concept_progress"

    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), primary_key=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mastery_level: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(16), index=True)  # gold | oil | crypto | stocks | macro
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    concept_ids: Mapped[list[int]] = mapped_column(JSON, default=list)


class LessonProgress(Base):
    __tablename__ = "lesson_progress"

    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16))  # article | concept | lesson
    source_id: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON)
    correct_index: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str | None] = mapped_column(Text)
    option_explanations: Mapped[list[str]] = mapped_column(JSON, default=list)
    difficulty: Mapped[str | None] = mapped_column(String(16))
    concept_slugs: Mapped[list[str]] = mapped_column(JSON, default=list)
    checker_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("quiz_questions.id", ondelete="CASCADE"), index=True)
    chosen_index: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReviewSchedule(Base):
    __tablename__ = "review_schedule"

    question_id: Mapped[int] = mapped_column(ForeignKey("quiz_questions.id", ondelete="CASCADE"), primary_key=True)
    next_review_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- alerts / briefings
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    condition: Mapped[str] = mapped_column(String(16))  # above | below | pct_move
    value: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    triggered_price: Mapped[float | None] = mapped_column(Float)


class Briefing(Base):
    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)  # Jakarta calendar date
    json_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --------------------------------------------------------------------------- system
class AICall(Base):
    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_started: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_finished: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))  # ok | error | running
    last_error: Mapped[str | None] = mapped_column(Text)
    run_count: Mapped[int] = mapped_column(Integer, default=0)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(String(200))

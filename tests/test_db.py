from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from app.db import migrations
from app.db.session import Database
from app.models.tables import AICall, SchemaVersion

EXPECTED_TABLES = {
    "assets", "quotes_latest", "price_snapshots", "candles", "news_articles", "article_assets", "breakdowns",
    "move_explanations", "concepts", "concept_progress", "lessons", "lesson_progress", "quiz_questions",
    "quiz_attempts", "review_schedule", "alerts", "briefings", "ai_calls", "provider_status",
    "calendar_events", "app_settings", "job_runs", "schema_version",
}


def test_fresh_database_gets_full_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    assert migrations.init_db(db) == migrations.LATEST_VERSION
    with db.engine.connect() as conn:
        assert EXPECTED_TABLES <= set(inspect(conn).get_table_names())
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_init_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    migrations.init_db(db)
    migrations.init_db(db)
    with db.session() as s:
        assert len(s.scalars(select(SchemaVersion)).all()) == 1


def test_utc_datetimes_round_trip_aware(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    migrations.init_db(db)
    when = datetime(2026, 11, 20, 3, 15, tzinfo=timezone.utc)
    with db.session() as s:
        s.add(AICall(purpose="t", model="m", success=True, created_at=when))
    with db.session() as s:
        got = s.scalars(select(AICall)).one().created_at
    assert got.tzinfo is not None and got == when


def test_naive_datetime_rejected(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    migrations.init_db(db)
    with pytest.raises(Exception):
        with db.session() as s:
            s.add(AICall(purpose="t", model="m", success=True, created_at=datetime(2026, 1, 1)))


def test_upgrade_keeps_data_and_makes_backup(tmp_path: Path, monkeypatch) -> None:
    """Simulate a future schema change: database at the current version with data -> next version
    adds a column; data survives and a backup is made."""
    db = Database(tmp_path / "x.db")
    current = migrations.init_db(db)
    with db.session() as s:
        s.add(AICall(purpose="keep-me", model="m", success=True, created_at=datetime.now(timezone.utc)))

    def next_version(conn) -> None:  # noqa: ANN001
        migrations.add_column_if_missing(conn, "ai_calls", "extra_note", "TEXT")

    monkeypatch.setattr(migrations, "LATEST_VERSION", current + 1)
    monkeypatch.setitem(migrations.MIGRATIONS, current + 1, next_version)
    assert migrations.init_db(db) == current + 1
    with db.engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("ai_calls")}
        assert "extra_note" in cols
        assert conn.execute(text("SELECT purpose FROM ai_calls")).scalar() == "keep-me"
        assert conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar() == current + 1
    backups = list((tmp_path / "backups").glob(f"x-v{current}-*.db"))
    assert len(backups) == 1


def test_stage0_database_upgrades_to_v2(tmp_path: Path) -> None:
    """A database created by Stage 0 (schema v1, no assets.decimals) upgrades in place."""
    db = Database(tmp_path / "old.db")
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE assets (id INTEGER PRIMARY KEY, symbol VARCHAR(32), name VARCHAR(128), "
                          "asset_class VARCHAR(16), provider_symbols JSON, market_schedule VARCHAR(32), currency VARCHAR(8), "
                          "enabled BOOLEAN, sort_order INTEGER, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at DATETIME, note VARCHAR(200))"))
        conn.execute(text("INSERT INTO schema_version VALUES (1, '2026-11-20 00:00:00', 'initial schema')"))
        conn.execute(text("INSERT INTO assets (id, symbol, name, asset_class, provider_symbols, market_schedule, currency, enabled, sort_order) "
                          "VALUES (1, 'BTC', 'Bitcoin', 'crypto', '{}', 'crypto_24_7', 'USD', 1, 0)"))
    assert migrations.init_db(db) == migrations.LATEST_VERSION
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT decimals FROM assets WHERE symbol='BTC'")).scalar() == 2
        assert EXPECTED_TABLES <= set(inspect(conn).get_table_names())

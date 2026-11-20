"""Simple versioned schema migrations (no Alembic).

How it works:
- A brand-new database gets every table at the latest schema and is stamped LATEST_VERSION.
- An existing database at version N runs migrations N+1 .. LATEST_VERSION in order. A copy of
  the database file is saved to data/backups/ first, so your data is never lost.
- `create_all` then adds any brand-new tables (it never touches existing tables).

To change the schema later: edit the models, bump LATEST_VERSION, and add a function to
MIGRATIONS that upgrades an existing database (e.g. ALTER TABLE ... ADD COLUMN).
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Connection, inspect, select, text

from app.db.session import Database
from app.models.tables import Base, SchemaVersion
from app.timeutil import utcnow

log = logging.getLogger(__name__)

LATEST_VERSION = 5


def add_column_if_missing(conn: Connection, table: str, column: str, ddl_type: str) -> None:
    """ALTER TABLE ADD COLUMN only if the column does not exist yet. A missing table is skipped:
    create_all() creates it afterwards with every current column."""
    if not inspect(conn).has_table(table):
        return
    cols = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in cols:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'))


def _v2_asset_decimals(conn: Connection) -> None:
    """Stage 1: price display precision per asset."""
    add_column_if_missing(conn, "assets", "decimals", "INTEGER NOT NULL DEFAULT 2")


def _v3_quote_change_basis(conn: Connection) -> None:
    """Stage 1: remember what a stored change is measured against (24h, prev close, previous day...)."""
    add_column_if_missing(conn, "quotes_latest", "change_basis", "VARCHAR(16)")


def _v4_news_flags(conn: Connection) -> None:
    """Stage 2: news processing flags."""
    add_column_if_missing(conn, "news_articles", "ai_tagged", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "news_articles", "body_attempted", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "news_articles", "body_error", "TEXT")
    add_column_if_missing(conn, "news_articles", "is_official", "BOOLEAN NOT NULL DEFAULT 0")


# version -> function that upgrades a database from (version - 1) to version.
MIGRATIONS: dict[int, Callable[[Connection], None]] = {
    2: _v2_asset_decimals,
    3: _v3_quote_change_basis,
    4: _v4_news_flags,
    5: lambda conn: (add_column_if_missing(conn, "news_articles", "image_url", "VARCHAR(2048)"),
                     add_column_if_missing(conn, "news_articles", "image_source", "VARCHAR(8)")),
}


def current_version(conn: Connection) -> int | None:
    if not inspect(conn).has_table("schema_version"):
        return None
    return conn.execute(select(SchemaVersion.version).order_by(SchemaVersion.version.desc()).limit(1)).scalar()


def _backup(db: Database, from_version: int) -> Path | None:
    if not db.path.exists():
        return None
    backup_dir = db.path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{db.path.stem}-v{from_version}-{stamp}{db.path.suffix}"
    with db.engine.connect() as conn:  # flush WAL into the main file before copying
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    shutil.copy2(db.path, target)
    return target


def init_db(db: Database) -> int:
    """Create or upgrade the schema. Returns the resulting schema version."""
    with db.engine.connect() as conn:
        version = current_version(conn)
        has_other_tables = bool(set(inspect(conn).get_table_names()) - {"schema_version"})

    if version is None and not has_other_tables:
        with db.engine.begin() as conn:
            Base.metadata.create_all(conn)
            conn.execute(
                SchemaVersion.__table__.insert().values(version=LATEST_VERSION, applied_at=utcnow(), note="initial schema")
            )
        log.info("Database created at schema version %s: %s", LATEST_VERSION, db.path)
        return LATEST_VERSION

    if version is None:
        # Tables exist but no version row (should not happen). Treat as version 1, the first schema.
        version = 1
        with db.engine.begin() as conn:
            Base.metadata.create_all(conn, tables=[SchemaVersion.__table__])
            conn.execute(SchemaVersion.__table__.insert().values(version=1, applied_at=utcnow(), note="stamped"))

    if version < LATEST_VERSION:
        backup = _backup(db, version)
        log.info("Upgrading database from v%s to v%s (backup: %s)", version, LATEST_VERSION, backup)
        for v in range(version + 1, LATEST_VERSION + 1):
            with db.engine.begin() as conn:
                MIGRATIONS[v](conn)
                conn.execute(SchemaVersion.__table__.insert().values(version=v, applied_at=utcnow(), note="migration"))
            log.info("Applied migration v%s", v)
    elif version > LATEST_VERSION:
        log.warning("Database schema v%s is newer than this code (v%s).", version, LATEST_VERSION)

    with db.engine.begin() as conn:
        Base.metadata.create_all(conn)  # new tables only; existing ones are untouched
    return max(version, LATEST_VERSION)

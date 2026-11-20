"""Custom column types."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.types import DateTime, TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores timezone-aware datetimes as UTC in SQLite and returns them as aware UTC.

    SQLite has no timezone support; without this, datetimes come back naive and it is easy
    to mix up UTC and Jakarta time. Naive datetimes are rejected on write.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given; use timezone-aware UTC datetimes")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)

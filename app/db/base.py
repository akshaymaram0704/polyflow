"""SQLAlchemy declarative base and shared column types."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Uses the generic ``JSON`` type so the same models work on both PostgreSQL
    (JSONB under the hood) and SQLite (used for offline/tests).
    """

    type_annotation_map = {dict: JSON, list: JSON}


def timestamp_column(**kwargs):
    """A timezone-aware DateTime column defaulting to now()."""
    return mapped_column(DateTime(timezone=True), default=utcnow, **kwargs)

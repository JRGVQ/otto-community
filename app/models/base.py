"""Shared model helpers."""
from __future__ import annotations

from datetime import datetime, UTC

from app.extensions import db


def utcnow() -> datetime:
    # Naive UTC (columns are stored without tz; keeps comparisons consistent).
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

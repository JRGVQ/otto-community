"""Typed access to platform-wide settings (the `platform_settings` key/value table).

Currently backs the log-retention window the owner sets in the backoffice. Reads are
best-effort: if the table doesn't exist yet (a deployment that hasn't migrated) or any
DB hiccup occurs, the coded default is returned so nothing ever 500s on a settings read.
"""
from __future__ import annotations

from flask import current_app

# Log retention (days) — how long request/error logs are kept before the daily prune.
LOG_RETENTION_KEY = "log_retention_days"
LOG_RETENTION_DEFAULT = 30
LOG_RETENTION_MIN = 1
LOG_RETENTION_MAX = 365


def get_setting(key: str, default: str | None = None) -> str | None:
    from app.extensions import db
    from app.models import PlatformSetting

    try:
        row = db.session.get(PlatformSetting, key)
        return row.value if row and row.value is not None else default
    except Exception:  # noqa: BLE001 — a settings read must never break a request
        db.session.rollback()
        return default


def set_setting(key: str, value: str) -> None:
    from app.extensions import db
    from app.models import PlatformSetting

    row = db.session.get(PlatformSetting, key)
    if row is None:
        row = PlatformSetting(key=key)
        db.session.add(row)
    row.value = value
    db.session.commit()


def log_retention_days() -> int:
    """Days to keep request/error logs. Owner-set (backoffice), falls back to the env
    default `LOG_RETENTION_DAYS`, then the coded 30. Always clamped to a sane range."""
    env_default = current_app.config.get("LOG_RETENTION_DAYS", LOG_RETENTION_DEFAULT)
    raw = get_setting(LOG_RETENTION_KEY, str(env_default))
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = LOG_RETENTION_DEFAULT
    return max(LOG_RETENTION_MIN, min(LOG_RETENTION_MAX, days))


def set_log_retention_days(days: int) -> int:
    """Persist the retention window (clamped). Returns the value actually stored."""
    days = max(LOG_RETENTION_MIN, min(LOG_RETENTION_MAX, int(days)))
    set_setting(LOG_RETENTION_KEY, str(days))
    return days

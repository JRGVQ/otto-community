"""Platform-wide key/value settings — global toggles the owner controls at runtime.

Unlike per-user `Setting` or per-org config, these are singletons for the whole
deployment (e.g. how many days the request/error logs are kept). Stored as strings;
typed access lives in `app.platform_settings`.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class PlatformSetting(TimestampMixin, db.Model):
    __tablename__ = "platform_settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)

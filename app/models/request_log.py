"""Per-request access log — the comprehensive activity trail behind the owner Logboek.

One row per served HTTP request across every environment (tenant + subdomain), so the
platform owner can trace exactly what happened before an error — including the 5xx's that
never reach the Python error handler (a proxy/worker timeout leaves no traceback, but the
request that triggered it is still recorded here). Retention is capped (see
`app.platform_settings.log_retention_days`) so the table can't grow unbounded.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import utcnow


class RequestLog(db.Model):
    __tablename__ = "request_logs"
    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=utcnow, index=True)
    site = db.Column(db.String(16))          # app / demo / docs / marketing (which omgeving)
    method = db.Column(db.String(8))
    path = db.Column(db.String(512))
    endpoint = db.Column(db.String(128))
    status_code = db.Column(db.Integer, index=True)
    duration_ms = db.Column(db.Integer)      # server-side handling time
    org_name = db.Column(db.String(128))
    user_email = db.Column(db.String(255))
    ip = db.Column(db.String(64))
    request_id = db.Column(db.String(36))

    @property
    def is_error(self) -> bool:
        return bool(self.status_code and self.status_code >= 500)

    @property
    def is_client_error(self) -> bool:
        return bool(self.status_code and 400 <= self.status_code < 500)

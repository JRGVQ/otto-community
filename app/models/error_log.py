"""Captured application errors — the data behind the owner debug console."""
from __future__ import annotations

from app.extensions import db
from app.models.base import utcnow


class ErrorLog(db.Model):
    __tablename__ = "error_logs"
    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=utcnow, index=True)
    level = db.Column(db.String(16), default="error")  # error / warning
    status_code = db.Column(db.Integer)
    method = db.Column(db.String(8))
    path = db.Column(db.String(512))
    endpoint = db.Column(db.String(128))
    exception_type = db.Column(db.String(128))
    message = db.Column(db.Text)
    traceback = db.Column(db.Text)
    user_email = db.Column(db.String(255))
    org_name = db.Column(db.String(128))
    request_id = db.Column(db.String(36))

    @property
    def short_message(self) -> str:
        return (self.message or "").splitlines()[0][:160] if self.message else ""

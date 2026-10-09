"""Audit trail of unsubscribe actions — one row per attempt.

Kept so there's a durable record of who unsubscribed from which sender, how (RFC 8058
one-click executed server-side, or the link/mailto handed to the browser), and whether
it succeeded. Mirrors the shape of EmailLog."""
from __future__ import annotations

from app.extensions import db
from app.models.base import utcnow


class UnsubscribeLog(db.Model):
    __tablename__ = "unsubscribe_logs"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # who unsubscribed
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id", ondelete="SET NULL"))

    sender = db.Column(db.String(128))
    sender_email = db.Column(db.String(255), index=True)
    method = db.Column(db.String(16))   # one_click | link | mailto | none
    target = db.Column(db.Text)         # the URL / mailto acted on
    status = db.Column(db.String(16))   # done | opened | failed | no_data
    detail = db.Column(db.Text)         # HTTP status or error reason
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

"""Editable transactional-email templates + a log of every send (with resend)."""
from __future__ import annotations

from app.extensions import db
from app.models.base import utcnow


class EmailTemplate(db.Model):
    """Owner override for a built-in email template. Absent row = use the code default
    (see email_templates() in app.email). Only subject + HTML are editable."""

    __tablename__ = "email_templates"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    subject = db.Column(db.Text)
    html = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class EmailLog(db.Model):
    """One row per outgoing-mail attempt — what was sent, to whom, and whether it
    actually went out. Stores the rendered HTML/text so the owner can re-send it."""

    __tablename__ = "email_logs"
    id = db.Column(db.Integer, primary_key=True)
    to_addr = db.Column(db.String(255), index=True)
    subject = db.Column(db.Text)
    template_key = db.Column(db.String(64))
    status = db.Column(db.String(16))  # sent | failed | no_mailer
    error = db.Column(db.Text)  # the provider's failure reason (token/permission/HTTP)
    html = db.Column(db.Text)
    text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

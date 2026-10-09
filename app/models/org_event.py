"""Org events — the business timeline of one customer environment.

Deliberately NOT the same thing as `AuditLog`. The audit log is a per-tenant trail that
quotes tenant content (sender names, knowledge-source names, colleagues' names), so it
must never be shown across tenants. An org event is a *business* fact about the account
itself — a plan changed, a mailbox was connected, a domain was verified — recorded with
a controlled vocabulary and no mailbox content, so the platform owner can follow the
health of an account without ever browsing a customer's data.

See app/events.py for the catalogue and the recorder.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import utcnow


class OrgEvent(db.Model):
    __tablename__ = "org_events"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    # A key from app.events.EVENT_LABELS — never free text, so the feed stays filterable
    # and can't accidentally start carrying tenant content.
    event = db.Column(db.String(48), nullable=False, index=True)
    # Short, safe context ("Personal → Business", "outlook"). Never mailbox content.
    detail = db.Column(db.String(255))
    actor = db.Column(db.String(64))  # display name, "systeem", or "platform-eigenaar"
    occurred_at = db.Column(db.DateTime, default=utcnow, index=True)

    organization = db.relationship("Organization")

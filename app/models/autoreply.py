"""Centraal beheerde afwezigheidsassistent (out-of-office) per postvak.

Otto verzendt niets: dit configureert Exchange's eigen automatische antwoord
(mailboxSettings.automaticRepliesSetting) — Exchange stuurt het OOO-bericht, niet Otto.
Vergt geen nieuwe scope: `MailboxSettings.ReadWrite` is al toegekend bij het koppelen.

De admin zet de gewenste stand hier; `push_state='pending'` markeert dat de sync ze naar
Graph moet duwen. Bij status 'scheduled' regelt Exchange zelf de aan/uit op de geplande
momenten — Otto hoeft de regel maar één keer te zetten.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class AutoReplySetting(TimestampMixin, db.Model):
    __tablename__ = "auto_reply_settings"
    __table_args__ = (db.UniqueConstraint("mail_account_id", name="uq_autoreply_account"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    mail_account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="disabled")  # disabled | scheduled | always
    scheduled_start = db.Column(db.DateTime)
    scheduled_end = db.Column(db.DateTime)
    internal_reply_html = db.Column(db.Text)
    external_reply_html = db.Column(db.Text)
    external_audience = db.Column(db.String(16), default="all")  # none | contactsOnly | all
    push_state = db.Column(db.String(16), nullable=False, default="pending")  # pending | synced | error
    last_pushed_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(255))

    account = db.relationship("MailAccount")

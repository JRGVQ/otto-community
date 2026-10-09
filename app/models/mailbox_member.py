"""Membership of a shared mailbox — the assigned 'team' who may see and draft for it.

A shared mailbox is a single `MailAccount` row (mailbox_type="shared"); the users
allowed to work it are its members. Only members see its mail (visibility is scoped
in app/web/context.py and app/api/_helpers.py).
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MailboxMember(TimestampMixin, db.Model):
    __tablename__ = "mailbox_members"
    id = db.Column(db.Integer, primary_key=True)
    mail_account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    can_draft = db.Column(db.Boolean, default=True)  # member may generate/edit drafts
    can_send = db.Column(db.Boolean, default=True)   # member may send, not just draft
    # Role *within this mailbox* (distinct from the org-level Role): owner configures
    # the mailbox, agent works it, viewer reads only. See constants.MAILBOX_ROLES.
    role = db.Column(db.String(16), default="agent")

    account = db.relationship("MailAccount", back_populates="members")
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("mail_account_id", "user_id", name="uq_mailbox_member"),
    )

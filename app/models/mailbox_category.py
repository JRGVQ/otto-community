"""Per-mailbox category configuration — fase 3 of the Teams environment.

A shared mailbox can opt into a SUBSET of the org's categories and override their
drafting behaviour and AI instruction, without touching the org templates. Absence
of a row means "inherit the org category" — so a freshly connected mailbox behaves
exactly as before until a manager configures it.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MailboxCategory(TimestampMixin, db.Model):
    __tablename__ = "mailbox_categories"
    id = db.Column(db.Integer, primary_key=True)
    mail_account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)

    enabled = db.Column(db.Boolean, default=True)        # category applies to this mailbox?
    draft_enabled = db.Column(db.Boolean, default=False)  # per-mailbox draft override
    ai_override = db.Column(db.Text)                      # extra instruction, appended to the org's

    # Per-mailbox foldering override (NULL = inherit the org category). Lets support@ and
    # info@ file the same category into their own folders.
    keep_in_inbox = db.Column(db.Boolean)                 # NULL = inherit Category.keep_in_inbox
    target_folder = db.Column(db.String(128))            # NULL = inherit Category.target_folder

    account = db.relationship("MailAccount", back_populates="mailbox_categories")
    category = db.relationship("Category")

    __table_args__ = (
        db.UniqueConstraint("mail_account_id", "category_id", name="uq_mailbox_category"),
    )


def effective_config(acct, category) -> tuple[bool, bool, str | None]:
    """(enabled, draft_enabled, ai_extra) for one category in a shared mailbox,
    inheriting the org category's defaults when no override row exists."""
    mc = next((m for m in acct.mailbox_categories if m.category_id == category.id), None)
    if mc is None:
        return True, bool(category.draft_enabled), None
    return bool(mc.enabled), bool(mc.draft_enabled), (mc.ai_override or None)


def effective_foldering(acct, category) -> tuple[bool, str | None]:
    """(keep_in_inbox, target_folder) for one category in a shared mailbox — the
    per-mailbox override wins, otherwise the org category's own foldering applies."""
    mc = next((m for m in acct.mailbox_categories if m.category_id == category.id), None)
    keep = category.keep_in_inbox if category.keep_in_inbox is not None else True
    folder = category.target_folder
    if mc is not None:
        if mc.keep_in_inbox is not None:
            keep = mc.keep_in_inbox
        if mc.target_folder:
            folder = mc.target_folder
    return bool(keep), folder

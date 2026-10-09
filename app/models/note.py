"""Internal team notes on a message — collaboration on shared mailboxes.

A note is never part of the e-mail and is never sent to the customer; it is internal
context for the team working a shared mailbox. Visible only to members (gated through
app/api/_helpers.py::get_message). Attached at message level because the sync pipeline
stores individual messages (threads are not materialised).
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MessageNote(TimestampMixin, db.Model):
    __tablename__ = "message_notes"
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)

    message = db.relationship(
        "Message",
        backref=db.backref("notes", cascade="all, delete-orphan", order_by="MessageNote.id"),
    )
    author = db.relationship("User")

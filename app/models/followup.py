"""Follow-up concepts — polite nudges for sent mail that got no reply.

Unlike a `Draft` (1:1 with an inbound Message), a follow-up replies to mail the user
themselves SENT, for which Otto keeps no Message row. So a follow-up is its own light
record: the sweep (app/tasks/followup.py) discovers unanswered sent threads via the
provider, drafts a reminder, and stores it here. The user sends it explicitly from the
Opvolgingen page — Otto never sends automatically.
"""
from __future__ import annotations

from app.constants import DRAFT_CONCEPT
from app.extensions import db
from app.models.base import TimestampMixin


class FollowUp(TimestampMixin, db.Model):
    __tablename__ = "follow_ups"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"), nullable=False)

    # The sent conversation being followed up on (Graph conversationId / Gmail threadId)
    # and the specific sent message the reminder replies to.
    provider_thread_id = db.Column(db.String(255), index=True)
    provider_message_id = db.Column(db.String(255))

    subject = db.Column(db.String(512))
    to_recipients = db.Column(db.String(512))     # comma-separated display of the recipients
    last_sent_at = db.Column(db.DateTime)          # when the user last sent in this thread

    status = db.Column(db.String(16), default=DRAFT_CONCEPT)  # concept / verzonden / verworpen
    current_text = db.Column(db.Text)
    provider_draft_id = db.Column(db.String(255))  # the reply draft placed at the provider

    account = db.relationship("MailAccount")
    user = db.relationship("User")

    @property
    def days_waiting(self) -> int | None:
        """Whole days since the last outbound message in this thread."""
        if not self.last_sent_at:
            return None
        from app.models.base import utcnow

        return max(0, (utcnow() - self.last_sent_at).days)

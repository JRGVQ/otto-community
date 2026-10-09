"""Concept replies.

A Draft has NO auto-send capability. `status` only becomes "verzonden" through the
explicit, permission-gated send route — never from the processing pipeline. This is
the structural half of Otto's "never sends automatically" guarantee.
"""
from __future__ import annotations

from app.constants import DRAFT_CONCEPT
from app.extensions import db
from app.models.base import TimestampMixin


class Draft(TimestampMixin, db.Model):
    __tablename__ = "drafts"
    # Postgres indexeert een foreign key niet vanzelf: zonder ix_drafts_message_id
    # scande elke concept-lookup (o.a. selectinload(Message.draft)) de hele tabel.
    __table_args__ = (
        db.Index("ix_drafts_message_id", "message_id"),
        db.Index("ix_drafts_status", "status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)

    status = db.Column(db.String(16), default=DRAFT_CONCEPT)  # concept / verzonden / verworpen
    current_tone = db.Column(db.String(16), default="neutraal")
    current_text = db.Column(db.Text)
    placed = db.Column(db.Boolean, default=True)  # written back to provider Drafts folder
    provider_draft_id = db.Column(db.String(255))
    lang = db.Column(db.String(48))

    message = db.relationship("Message", back_populates="draft")
    tone_variants = db.relationship(
        "DraftToneVariant", back_populates="draft", cascade="all, delete-orphan"
    )

    def variant(self, tone: str) -> str | None:
        for v in self.tone_variants:
            if v.tone == tone:
                return v.text
        return None


class DraftToneVariant(db.Model):
    __tablename__ = "draft_tone_variants"
    id = db.Column(db.Integer, primary_key=True)
    draft_id = db.Column(db.Integer, db.ForeignKey("drafts.id"), nullable=False)
    tone = db.Column(db.String(16), nullable=False)  # neutraal / formeel / kort / vriendelijk
    text = db.Column(db.Text)

    draft = db.relationship("Draft", back_populates="tone_variants")

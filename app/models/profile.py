"""Per-user writing style profile and learning feedback signal."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class StyleProfile(db.Model):
    __tablename__ = "style_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    tone = db.Column(db.String(32), default="neutraal")  # the user's preferred draft tone
    length = db.Column(db.String(32), default="middel")
    greeting = db.Column(db.String(64), default="Hallo")
    signoff = db.Column(db.String(64), default="Groeten")
    signature = db.Column(db.Text)
    default_reply = db.Column(db.Text)  # "standaard antwoord" — guidance Otto applies to every draft
    learned_at = db.Column(db.DateTime)  # when the style was last learned from the user's mail

    # Follow-ups: auto-draft a polite nudge when a sent mail got no reply after N days.
    follow_up_enabled = db.Column(db.Boolean, default=False)
    follow_up_days = db.Column(db.Integer, default=3)
    # Housekeeping: discard unsent concept drafts older than N days (0/NULL = keep forever).
    draft_ttl_days = db.Column(db.Integer)
    # Auto-foldering: move mail in "move out of inbox" categories to their target folder.
    auto_folder_enabled = db.Column(db.Boolean, default=False)
    # Ruisfilter: bulkmail (List-Unsubscribe / no-reply) meteen in de ruiscategorie, zonder
    # AI-classificatie en zonder concept. Standaard uit — zie app/noise.py.
    noise_filter_enabled = db.Column(db.Boolean, default=False)
    # Digest-modus: de inbox toont de stapel van het laatste vaste moment; wat daarna
    # binnenkwam wacht op het volgende. Zie app/inbox_digest.py.
    digest_mode = db.Column(db.Boolean, default=False)
    digest_hours = db.Column(db.String(32))  # "9,17" — leeg = de standaardmomenten
    # Agenda: vrije momenten voorstellen in een concept dat om een afspraak vraagt.
    # Opt-in — je vrij/bezet is van jou. Zie app/scheduling.py.
    propose_slots_enabled = db.Column(db.Boolean, default=False)
    meeting_minutes = db.Column(db.Integer)   # duur van een voorstel; leeg = 30
    # Toezeggingen: "ik bezorg je dat morgen" in verzonden mail wordt een taak met
    # deadline. Opt-in — zie app/commitments.py.
    commitments_enabled = db.Column(db.Boolean, default=False)
    # Vergadertranscripties: actiepunten eruit halen en op de takenlijst zetten. Opt-in —
    # zie app/transcripts.py.
    transcripts_enabled = db.Column(db.Boolean, default=False)

    raw_profile = db.Column(db.JSON, default=dict)

    user = db.relationship("User", back_populates="style_profile")


class Feedback(TimestampMixin, db.Model):
    """Learning signal: category corrections and draft edits."""

    __tablename__ = "feedback"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"))
    kind = db.Column(db.String(32))  # recategorize / draft_edit
    from_value = db.Column(db.Text)
    to_value = db.Column(db.Text)

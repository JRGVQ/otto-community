"""Per-mailbox configuration — the 'team settings' for a shared mailbox.

A shared mailbox (MailAccount with mailbox_type="shared") gets its own voice,
signature and behaviour here, independent of the connecting member's personal
StyleProfile. 1:1 satellite of MailAccount — mirrors how AiConfig/Billing/SsoConfig
satellite an Organization. Personal mailboxes never get a row.

Fase 1 stores the settings; the AI pipeline starts honouring them in fase 2
(see app/tasks/draft.py / app/tasks/pipeline.py).
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MailboxSettings(TimestampMixin, db.Model):
    __tablename__ = "mailbox_settings"
    id = db.Column(db.Integer, primary_key=True)
    mail_account_id = db.Column(
        db.Integer, db.ForeignKey("mail_accounts.id"), nullable=False, unique=True
    )

    # Profile
    display_name = db.Column(db.String(128))            # friendly name; falls back to the address
    color = db.Column(db.String(9), default="#6C4CE6")
    description = db.Column(db.Text)

    # Drafting behaviour (honoured by the pipeline from fase 2 on)
    auto_draft = db.Column(db.Boolean, default=True)    # generate concepts for this mailbox?
    tone = db.Column(db.String(32), default="neutraal")  # mailbox voice (overrides personal)
    signature = db.Column(db.Text)                      # team sign-off
    default_reply = db.Column(db.Text)                  # standing instruction applied to every draft
    language = db.Column(db.String(48), default="antwoord in Nederlands")

    # Grounding: which knowledge source this mailbox uses (NULL = org default)
    knowledge_source_id = db.Column(db.Integer, db.ForeignKey("knowledge_sources.id"))

    # Service rules
    office_hours = db.Column(db.JSON, default=dict)     # {"start": "09:00", "end": "18:00", ...}
    sla_minutes = db.Column(db.Integer)                 # expected response time
    approval_required = db.Column(db.Boolean, default=False)  # draft must be approved before send

    # Follow-ups / housekeeping / auto-foldering — mailbox-level mirror of the personal
    # StyleProfile settings, so a shared mailbox gets its own behaviour.
    follow_up_enabled = db.Column(db.Boolean, default=False)
    follow_up_days = db.Column(db.Integer, default=3)
    draft_ttl_days = db.Column(db.Integer)              # discard unsent concepts older than N days
    auto_folder_enabled = db.Column(db.Boolean, default=False)
    noise_filter_enabled = db.Column(db.Boolean, default=False)  # zie app/noise.py
    propose_slots_enabled = db.Column(db.Boolean, default=False)  # zie app/scheduling.py
    meeting_minutes = db.Column(db.Integer)
    commitments_enabled = db.Column(db.Boolean, default=False)  # zie app/commitments.py
    transcripts_enabled = db.Column(db.Boolean, default=False)  # zie app/transcripts.py

    # Microsoft Teams integration (fase 6) — post mailbox events to a channel via an
    # Incoming Webhook URL the admin pastes. No extra Graph scopes needed.
    teams_webhook_url = db.Column(db.Text)
    teams_notify = db.Column(db.Boolean, default=False)
    # Bundelen: in plaats van één kaart per mail (wat een druk kanaal onbruikbaar maakt)
    # verzamelt Otto de meldingen en post ze op vaste momenten als één overzicht.
    # Zie app/teams_bundle.py + app/tasks/teams_notify.py.
    teams_bundle = db.Column(db.Boolean, default=False)
    teams_bundle_hours = db.Column(db.String(32))   # "9,13,17" — leeg = de standaardmomenten
    teams_last_flush_at = db.Column(db.DateTime)     # laatste keer dat de wachtrij geleegd is

    account = db.relationship("MailAccount", back_populates="settings")
    knowledge_source = db.relationship("KnowledgeSource")

    @property
    def name(self) -> str:
        """Best display label for the mailbox."""
        if self.display_name:
            return self.display_name
        acct = self.account
        return (acct.target_address or acct.email) if acct else "Gedeeld postvak"

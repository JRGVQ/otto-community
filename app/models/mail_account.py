"""Connected mailbox accounts (OAuth tokens stored encrypted at rest)."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class MailAccount(TimestampMixin, db.Model):
    __tablename__ = "mail_accounts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Set explicitly so an org-level shared mailbox stays queryable and survives the
    # connecting user being offboarded. Personal accounts: backfilled from user.organization_id.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))

    provider = db.Column(db.String(16), nullable=False)  # gmail / outlook / imap
    email = db.Column(db.String(255))
    status = db.Column(db.String(16), default="connected")  # connected / enabled / error
    # Last sync/connection failure (provider message + HTTP code); NULL when healthy.
    # Set by sync_account / the connection test and surfaced on the Integraties page.
    last_error = db.Column(db.Text)

    # Personal mailbox vs. an Exchange shared mailbox the user has delegate access to.
    mailbox_type = db.Column(db.String(16), default="personal")  # personal / shared
    target_address = db.Column(db.String(255))  # shared mailbox UPN; NULL for personal
    # For a shared mailbox: the member's personal account whose delegated Graph token is
    # used to reach it. Re-pointable to another member when the connector is offboarded.
    delegated_via_account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"))

    # Tokens encrypted via app.security.crypto before storage.
    access_token_enc = db.Column(db.Text)
    refresh_token_enc = db.Column(db.Text)
    token_expiry = db.Column(db.DateTime)

    # Gmail historyId (short) OR a Microsoft Graph deltaLink. The latter is a full URL
    # carrying an opaque folder-id + $deltatoken and routinely runs 350-500 chars, so
    # this MUST be unbounded Text: a String(255) silently works on SQLite (dev) but
    # raises "value too long" on Postgres (prod), failing every Outlook sync.
    sync_cursor = db.Column(db.Text)
    subscription_id = db.Column(db.String(255))
    watch_expiry = db.Column(db.DateTime)
    last_sync = db.Column(db.String(64))           # human display ("zojuist", "5 min geleden")
    last_sync_at = db.Column(db.DateTime)          # machine timestamp the scheduler reasons over
    # How often the background scheduler polls this mailbox. NULL → use SYNC_INTERVAL_SECONDS.
    sync_interval_seconds = db.Column(db.Integer)
    provider_config = db.Column(db.JSON, default=dict)
    # Microsoft 365-groepslidmaatschappen (Graph-GUID's) van dit postvak, gecachet zodat
    # de handtekening-toewijzing per groep geen Graph-call per concept kost. Vernieuwd
    # tijdens de sync (±daags); leeg zonder GroupMember.Read.All-consent — dan matcht
    # een groepstoewijzing gewoon niet.
    graph_group_ids = db.Column(db.JSON, default=list)
    graph_groups_synced_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="mail_accounts")
    organization = db.relationship("Organization")
    # Self-reference: a shared account points at the personal account whose token it borrows.
    delegated_via = db.relationship(
        "MailAccount", remote_side=[id], backref="shared_children"
    )
    members = db.relationship(
        "MailboxMember", back_populates="account", cascade="all, delete-orphan"
    )
    # Per-mailbox config for shared mailboxes (1:1). Cascade so the GDPR delete in
    # api/shared_mailboxes.leave_shared also clears the settings row.
    settings = db.relationship(
        "MailboxSettings", back_populates="account", uselist=False,
        cascade="all, delete-orphan",
    )
    # Per-mailbox category config (fase 3); absence of a row = inherit the org category.
    mailbox_categories = db.relationship(
        "MailboxCategory", back_populates="account", cascade="all, delete-orphan"
    )
    threads = db.relationship(
        "Thread", back_populates="account", cascade="all, delete-orphan"
    )
    teams_pending = db.relationship(
        "TeamsPending", back_populates="account", cascade="all, delete-orphan"
    )
    staged_appointments = db.relationship(
        "StagedAppointment", back_populates="account", cascade="all, delete-orphan"
    )
    messages = db.relationship(
        "Message", back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def is_shared(self) -> bool:
        return self.mailbox_type == "shared"

    @property
    def is_mock(self) -> bool:
        """A demo/seed mailbox: mock data, no provider grant behind it.

        It is flagged 'connected' so the demo looks real, but there is no token to
        use — syncing one only produces a ProviderAuthError, so every scheduled and
        manual sync skips it (see app.tasks.sync.sync_account).
        """
        cfg = self.provider_config or {}
        return bool(cfg.get("demo") or cfg.get("seed"))

    @property
    def member_user_ids(self) -> list[int]:
        return [m.user_id for m in self.members]

"""Threads and messages."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Thread(TimestampMixin, db.Model):
    __tablename__ = "threads"
    # `_get_or_create_thread` zoekt per binnenkomend bericht op dit paar; zonder index
    # is dat een volledige scan van `threads` voor ELKE mail tijdens de delta-sync.
    __table_args__ = (
        db.Index("ix_threads_account_provider", "account_id", "provider_thread_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"))
    provider_thread_id = db.Column(db.String(255))
    subject = db.Column(db.String(512))
    summary = db.Column(db.Text)

    account = db.relationship("MailAccount", back_populates="threads")
    messages = db.relationship(
        "Message", back_populates="thread", cascade="all, delete-orphan"
    )


class Message(TimestampMixin, db.Model):
    __tablename__ = "messages"
    # Waar de inbox werkelijk op filtert en sorteert. Zonder deze indexen scande elke
    # render van inbox/briefing/analyse de héle tabel — over alle tenants heen.
    __table_args__ = (
        db.Index("ix_messages_org_archived_received", "organization_id", "archived", "received_at"),
        db.Index("ix_messages_account_id", "account_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    account_id = db.Column(db.Integer, db.ForeignKey("mail_accounts.id"))
    thread_id = db.Column(db.Integer, db.ForeignKey("threads.id"))

    provider_message_id = db.Column(db.String(255), index=True)
    # Raw provider conversation id (Graph conversationId / Gmail threadId). Stored so the
    # "already answered" guard can match a whole conversation — the thread_id FK above is
    # left NULL for synced mail, so it can't be used for that.
    provider_thread_id = db.Column(db.String(255), index=True)
    sender = db.Column(db.String(128))
    sender_email = db.Column(db.String(255))
    initials = db.Column(db.String(4))
    avatar_color = db.Column(db.String(9))
    time_display = db.Column(db.String(32))  # "08:42" / "gisteren" / "ma"
    received_at = db.Column(db.DateTime)
    unread = db.Column(db.Boolean, default=True)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    subcategory_id = db.Column(db.Integer, db.ForeignKey("subcategories.id"))
    # Wie deze mail in zijn categorie legde: "filter" (ruisfilter, zonder AI-aanroep),
    # "ai" (LLM-classificatie) of "user" (handmatig verplaatst). NULL = van vóór deze
    # kolom — dan weten we het niet, en telt het ruis-rapport hem niet als besparing.
    classified_by = db.Column(db.String(16))

    subject = db.Column(db.String(512))
    snippet = db.Column(db.String(512))
    body = db.Column(db.Text)
    # De originele HTML-body zoals de provider hem leverde (Outlook levert altijd HTML).
    # NULL voor mail van vóór deze kolom en voor providers zonder HTML; de inbox valt
    # dan terug op de tekstweergave. Sanitizen gebeurt bij weergave (app/htmlview.py),
    # nooit bij opslag — zo profiteert oude mail mee van een strengere sanitizer later.
    # Deferred: alleen het geopende gesprek leest deze kolom (_thread_html_map), terwijl
    # _messages() álle zichtbare mail laadt — zonder defer sleept elke inbox-, briefing-
    # en analyseweergave megabytes marketing-HTML mee die niemand toont.
    body_html = db.deferred(db.Column(db.Text))
    lang = db.Column(db.String(48), default="antwoord in Nederlands")
    archived = db.Column(db.Boolean, default=False)
    is_seed = db.Column(db.Boolean, default=False)

    # List-Unsubscribe (RFC 2369 / 8058) — how to unsubscribe from this sender's list.
    # Parsed at ingestion from the mail headers; drives the "Uitschrijven" action.
    unsubscribe_url = db.Column(db.String(1024))     # http(s) unsubscribe endpoint
    unsubscribe_mailto = db.Column(db.String(512))   # mailto: unsubscribe URI
    unsubscribe_oneclick = db.Column(db.Boolean, default=False)  # RFC 8058 one-click POST
    unsubscribed_at = db.Column(db.DateTime)         # set once the user unsubscribes

    # Collaboration (fase 4) — who's handling this mail and where it stands. Mainly
    # used in shared mailboxes; personal mail just stays "open" until archived/sent.
    assignee_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    state = db.Column(db.String(16), default="open")  # open / in_progress / awaiting_reply / done
    replied_at = db.Column(db.DateTime)  # when we sent a reply — anchors follow-up + "wacht op antwoord"

    account = db.relationship("MailAccount", back_populates="messages")
    assignee = db.relationship("User", foreign_keys=[assignee_user_id])
    thread = db.relationship("Thread", back_populates="messages")
    category = db.relationship("Category", back_populates="messages")
    subcategory = db.relationship("Subcategory")
    draft = db.relationship(
        "Draft", back_populates="message", uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def has_concept_draft(self) -> bool:
        return bool(self.draft and self.draft.status == "concept")

    @property
    def has_unsubscribe(self) -> bool:
        """True when this mail carries a usable List-Unsubscribe link (mailto or URL)."""
        return bool(self.unsubscribe_url or self.unsubscribe_mailto)

    def visible_to(self, user) -> bool:
        """Tenant + ownership scoping for a single message.

        Personal mailbox mail is private to its owner; a shared mailbox's mail is
        visible to its assigned members. Mirrors the list scoping in
        app/web/context.py:_messages().
        """
        if self.organization_id != user.organization_id:
            return False
        acct = self.account
        if acct is None:
            return self.user_id == user.id  # account-less (seed) mail → its own owner
        if acct.is_shared:
            return user.id in acct.member_user_ids
        return acct.user_id == user.id  # personal mailbox → the user who owns it

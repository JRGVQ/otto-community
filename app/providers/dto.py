"""Plain data-transfer objects exchanged with provider adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MessageDTO:
    provider_message_id: str
    thread_id: str | None
    sender: str
    sender_email: str
    subject: str
    snippet: str
    body: str
    # De originele HTML-body (alleen providers die HTML leveren; Outlook altijd).
    # Gaat ongewijzigd de database in; sanitizen gebeurt pas bij weergave.
    body_html: str | None = None
    received_at: datetime | None = None
    unread: bool = True
    lang: str = "antwoord in Nederlands"
    # Raw List-Unsubscribe headers (RFC 2369 / 8058), parsed downstream at persist time.
    list_unsubscribe: str | None = None
    list_unsubscribe_post: str | None = None
    # True for a delta "@removed" entry: the message left the mailbox INBOX (deleted,
    # archived or moved on the server). Carries only provider_message_id; sync archives
    # the local copy so Otto's inbox mirrors the live mailbox.
    removed: bool = False


@dataclass
class ThreadDTO:
    provider_thread_id: str
    subject: str
    messages: list[MessageDTO] = field(default_factory=list)


@dataclass
class DraftDTO:
    provider_draft_id: str
    thread_id: str | None
    subject: str
    body: str

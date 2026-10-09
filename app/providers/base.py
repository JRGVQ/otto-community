"""The MailProvider contract.

NOTE the deliberate absence of any `send()` method here. The categorize/draft
pipeline is given only a `MailProvider`, so there is no code path from automated
processing to transmitting mail. Sending is opt-in, user-initiated, and isolated
in `UserInitiatedSender`, used solely by the permission-gated `/api/drafts/<id>/send`
route.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.providers.dto import MessageDTO, ThreadDTO


class MailProvider(ABC):
    def __init__(self, account) -> None:
        self.account = account

    # ── Capability ────────────────────────────────────────────────────────
    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Whether this provider's app credentials + SDK are available."""

    # ── Auth ──────────────────────────────────────────────────────────────
    @abstractmethod
    def authenticate(self) -> None: ...

    @abstractmethod
    def refresh_token(self) -> None: ...

    # ── Read ──────────────────────────────────────────────────────────────
    @abstractmethod
    def list_recent(self, limit: int = 300) -> list[MessageDTO]:
        """Initial import used to learn style + sort preferences."""

    @abstractmethod
    def fetch_new(self, cursor: str | None) -> tuple[list[MessageDTO], str | None]:
        """Incremental fetch; returns (messages, new_cursor)."""

    @abstractmethod
    def get_thread(self, thread_id: str) -> ThreadDTO: ...

    # ── Write-back (labels + drafts only — never send) ───────────────────
    @abstractmethod
    def apply_label(self, provider_message_id: str, category_name: str, color: str | None = None,
                    replace_bases: set[str] | None = None) -> None:
        """Zet het categorielabel op een bericht. `replace_bases` draagt de kale
        Otto-categorienamen van de organisatie: labels met zo'n basisnaam (elk
        nummer-voorvoegsel telt mee) worden vervangen — één Otto-categorie per
        bericht; de eigen labels van de gebruiker blijven staan."""
        ...

    @abstractmethod
    def create_draft(
        self, thread_id: str | None, subject: str, body: str,
        in_reply_to: str | None = None, references: str | None = None,
        signature_html: str | None = None, signature_text: str | None = None,
    ) -> str:
        """Create a reply DRAFT in the provider's Drafts folder. Returns its id.

        This is the only reply-writing operation the pipeline may perform.

        `signature_html`/`signature_text` dragen een door de organisatie beheerde
        handtekening die ONDER de conceptekst hoort. HTML-providers plakken de
        HTML-variant onder de body; tekst-providers de tekstvariant. Beide None =
        geen server-side handtekening (dan zit hij al in `body`, zoals vroeger).
        """

    # ── Sync baseline (optional; "only new mail from now on") ───────────────
    # ── Inbox-regels ─────────────────────────────────────────────────────────
    # Alleen providers die serverregels kennen implementeren dit; `supports_rules`
    # laat de UI weten of het scherm iets te bieden heeft voor deze mailbox.
    supports_rules: bool = False

    def list_rules(self) -> list[dict]:
        return []

    def create_rule(self, payload: dict) -> str | None:
        return None

    def update_rule(self, rule_id: str, payload: dict) -> None: ...

    def delete_rule(self, rule_id: str) -> None: ...

    def inbox_message_ids(self) -> set[str] | None:
        """De provider-id's die op dit moment in de INBOX staan, of None.

        Bedoeld om de spiegel te kunnen herstellen: alles wat Otto nog toont maar hier
        niet in zit, is intussen verplaatst of verwijderd. None betekent "kan ik niet
        bepalen" — dan wordt er niets gearchiveerd, want een lege set zou alles wissen.
        """
        return None

    def delta_baseline(self) -> str | None:
        """Capture the CURRENT sync cursor without returning any messages, so the next
        `fetch_new` only yields mail that arrives afterwards (no backlog). Default: fall
        back to a full fetch_new and keep just its cursor."""
        _, cursor = self.fetch_new(None)
        return cursor

    # ── Already-answered check (optional; skip drafting handled threads) ─────
    def has_sent_reply_after(self, conversation_id: str, after_dt=None) -> bool:
        """True if the mailbox owner already SENT a message in this conversation at/after
        `after_dt` — i.e. they've replied, so Otto shouldn't draft another. Default: unknown
        → False (never block drafting on a provider that can't check)."""
        return False

    # ── Draft replace (optional; avoid stacking duplicate drafts) ────────────
    def delete_draft(self, provider_draft_id: str) -> None:
        """Delete a previously-created draft (so a regenerated one replaces it). Default: no-op."""
        return None

    # ── Folders (optional; for the auto-foldering suggestion + move) ─────────
    def list_folders(self) -> list[dict]:
        """The mailbox's user folders as [{'id', 'name'}]. Default: none."""
        return []

    def move_to_folder(self, provider_message_id: str, folder_id: str) -> None:
        """Move a message into a folder. Default: no-op (provider may not support it)."""
        return None

    def ensure_folder(self, name: str) -> str | None:
        """Return the id of the mailbox folder called `name`, creating it if needed.

        Used by auto-foldering to move mail out of the inbox. Default: match an existing
        folder by (case-insensitive) name via `list_folders()`; return None if absent
        (a provider that can't create folders simply skips the move)."""
        want = (name or "").strip().lower()
        if not want:
            return None
        for f in self.list_folders():
            if (f.get("name") or "").strip().lower() == want:
                return f.get("id")
        return None

    # ── Follow-ups (optional; nudge sent mail that got no reply) ─────────────
    def list_awaiting_reply(self, min_age_days: int, max_age_days: int, limit: int = 25) -> list[dict]:
        """Sent messages with no inbound reply since, aged between the two bounds.

        Each item: {'conversation_id', 'provider_message_id', 'subject', 'to',
        'sent_at' (datetime), 'body'}. Default: none (provider can't check)."""
        return []

    def create_followup_draft(self, provider_message_id: str, body: str,
                              signature_html: str | None = None) -> str | None:
        """Create a reply-all DRAFT to a previously SENT message, carrying the reminder
        text. Returns the draft id. Default: unsupported → None. Never sends."""
        return None

    # ── HTML-weergave (optioneel; alleen providers die HTML kunnen leveren) ──
    def get_body_html(self, provider_message_id: str) -> str | None:
        """De originele HTML-body van één bericht, of None. Gebruikt voor de luie
        backfill van mail die vóór de body_html-kolom gesynchroniseerd is."""
        return None

    def get_inline_attachment(self, provider_message_id: str, content_id: str) -> tuple[bytes, str] | None:
        """(bytes, mimetype) van een inline-bijlage (cid:…), of None. On demand
        opgehaald — inline afbeeldingen worden nooit in de database bewaard."""
        return None

    # ── Realtime ──────────────────────────────────────────────────────────
    def start_watch(self) -> None:  # optional
        ...

    def stop_watch(self) -> None:  # optional
        ...


class UserInitiatedSender(ABC):
    """Mixin granting an explicit, user-triggered send. Never used by the pipeline."""

    @abstractmethod
    def send_user_draft(self, provider_draft_id: str) -> None:
        """Transmit a draft the user explicitly approved. Requires the sendMail scope."""

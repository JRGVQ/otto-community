"""Generic IMAP/SMTP adapter — read via IMAP, write replies by APPEND to Drafts.

SMTP is wired only behind the explicit user-send route; the pipeline never sends.
Per-account credentials live (encrypted) in the MailAccount provider_config.
"""
from __future__ import annotations

import imaplib
from datetime import datetime
from email.message import EmailMessage

from app.providers.base import MailProvider, UserInitiatedSender
from app.providers.dto import MessageDTO, ThreadDTO
from app.providers.exceptions import ProviderNotConfigured


class ImapProvider(MailProvider, UserInitiatedSender):
    @classmethod
    def is_configured(cls) -> bool:
        return True  # configured per-account, validated at connect time

    def _config(self) -> dict:
        cfg = self.account.provider_config or {}
        if not cfg.get("host"):
            raise ProviderNotConfigured("IMAP-account is nog niet ingesteld (host/login ontbreekt).")
        return cfg

    def _connect(self) -> imaplib.IMAP4_SSL:
        from app.security.crypto import decrypt

        cfg = self._config()
        conn = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("imap_port", 993)))
        conn.login(cfg["username"], decrypt(cfg.get("password_enc")))
        return conn

    def authenticate(self) -> None:
        self._connect().logout()

    def refresh_token(self) -> None:
        pass  # app-password based, no token to refresh

    def list_recent(self, limit: int = 300) -> list[MessageDTO]:
        conn = self._connect()
        try:
            conn.select("INBOX")
            _, data = conn.search(None, "ALL")
            ids = data[0].split()[-limit:]
            out: list[MessageDTO] = []
            for mid in ids:
                _, msg_data = conn.fetch(mid, "(RFC822)")
                out.append(self._to_dto(mid.decode(), msg_data[0][1]))
            return out
        finally:
            conn.logout()

    def fetch_new(self, cursor: str | None) -> tuple[list[MessageDTO], str | None]:
        msgs = self.list_recent(limit=25)
        newest = msgs[-1].provider_message_id if msgs else cursor
        return msgs, newest

    def get_thread(self, thread_id: str) -> ThreadDTO:
        return ThreadDTO(provider_thread_id=thread_id, subject="", messages=[])

    def apply_label(self, provider_message_id: str, category_name: str, color: str | None = None,
                    replace_bases: set[str] | None = None) -> None:
        # IMAP has no universal labels; categorisation is kept app-side (and via folders).
        pass

    def create_draft(self, thread_id, subject, body, in_reply_to=None, references=None,
                     signature_html=None, signature_text=None) -> str:
        conn = self._connect()
        try:
            mime = EmailMessage()
            mime["Subject"] = subject
            if in_reply_to:
                mime["In-Reply-To"] = in_reply_to
                mime["References"] = references or in_reply_to
            # IMAP-concepten zijn platte tekst; een org-handtekening gaat als tekst mee.
            if signature_text:
                body = f"{body}\n\n{signature_text}"
            mime.set_content(body)
            conn.append("Drafts", "\\Draft", imaplib.Time2Internaldate(datetime.now().timestamp()), mime.as_bytes())
            return f"imap-draft-{subject[:24]}"
        finally:
            conn.logout()

    def send_user_draft(self, provider_draft_id: str) -> None:
        # Explicit user action: relay the stored draft over SMTP. Pipeline never calls this.
        raise ProviderNotConfigured("SMTP-verzending vereist expliciete configuratie.")

    def _to_dto(self, uid: str, raw: bytes) -> MessageDTO:
        import email

        msg = email.message_from_bytes(raw)
        sender = msg.get("From", "")
        name, _, addr = sender.partition("<")
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="replace")
        return MessageDTO(
            provider_message_id=uid, thread_id=msg.get("Message-ID"),
            sender=name.strip().strip('"') or addr.strip(">"), sender_email=addr.strip(">"),
            subject=msg.get("Subject", "(geen onderwerp)"), snippet=body[:140], body=body,
            list_unsubscribe=msg.get("List-Unsubscribe"),
            list_unsubscribe_post=msg.get("List-Unsubscribe-Post"),
        )

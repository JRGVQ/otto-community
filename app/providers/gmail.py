"""Gmail adapter — scope `gmail.modify` only (read, label, create drafts). No send.

Realtime via `users.watch` + Pub/Sub. SDK and credentials are imported lazily so
the app runs without google-api-python-client installed.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage

from flask import current_app

from app.providers.base import MailProvider, UserInitiatedSender
from app.providers.dto import MessageDTO, ThreadDTO
from app.providers.exceptions import ProviderAuthError, ProviderNotConfigured

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailProvider(MailProvider, UserInitiatedSender):
    def _org(self):
        user = getattr(self.account, "user", None)
        return user.organization if user is not None else None

    def is_configured(self) -> bool:
        """Can THIS mailbox's organisation talk to Gmail?

        Resolved per-org, exactly like Outlook: a Connect customer brings its own Google
        app, and the platform credentials may not be set at all on that deployment. Asking
        the platform flag instead let such a customer complete the OAuth connect and then
        fail every single sync with "Gmail is niet geconfigureerd".
        """
        from app.connectors import gmail_configured_for

        if not gmail_configured_for(self._org()):
            return False
        try:  # SDK present?
            import googleapiclient  # noqa: F401
        except ImportError:
            return False
        return True

    # ── Credentials ───────────────────────────────────────────────────────
    def _credentials(self):
        """The stored grant, bound to the OAuth app that actually issued it.

        The client id/secret must be the org's own app when it has one. Refreshing a
        token against a different client is rejected by Google (`invalid_client`), so
        reading the platform credentials here broke every BYO mailbox an hour after it
        was connected — the lifetime of one access token.
        """
        from app.connectors import google_app
        from app.security.crypto import decrypt

        # Diagnose the configuration before touching the SDK: Google's libraries are an
        # optional dependency, and an ImportError here would mask the real reason.
        client_id, client_secret = google_app(self._org())
        if not (client_id and client_secret):
            raise ProviderNotConfigured("Gmail is niet geconfigureerd voor deze omgeving.")
        access, refresh = decrypt(self.account.access_token_enc), decrypt(self.account.refresh_token_enc)
        if not (access or refresh):
            raise ProviderAuthError("Geen geldige Google-token; koppel het postvak opnieuw.")

        from google.oauth2.credentials import Credentials

        return Credentials(
            token=access, refresh_token=refresh, token_uri=TOKEN_URI,
            client_id=client_id, client_secret=client_secret, scopes=GMAIL_SCOPES,
        )

    def _refresh_now(self, creds) -> None:
        """Exchange the refresh token and store what comes back.

        google-auth refreshes in-memory during a call, which is why the old code looked
        like it worked: the request went through and the fresh token was then thrown
        away, so every call paid for a refresh and the stored token stayed stale forever
        (and `token_expiry` was never written at all).
        """
        from google.auth.transport.requests import Request

        from app.extensions import db
        from app.security.crypto import encrypt

        if not creds.refresh_token:
            raise ProviderAuthError("Google-token verlopen zonder refresh-token; koppel het postvak opnieuw.")
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 — surface as an auth problem, not a 500
            raise ProviderAuthError(f"Google-token vernieuwen mislukt: {exc}") from exc
        self.account.access_token_enc = encrypt(creds.token)
        if creds.refresh_token:
            self.account.refresh_token_enc = encrypt(creds.refresh_token)
        # google-auth hands back a naive UTC datetime, which is what the column stores.
        self.account.token_expiry = getattr(creds, "expiry", None)
        db.session.commit()

    def _service(self):
        if not self.is_configured():
            raise ProviderNotConfigured("Gmail is niet geconfigureerd (credentials/SDK ontbreken).")
        from googleapiclient.discovery import build

        creds = self._credentials()
        if not creds.token or creds.expired:
            self._refresh_now(creds)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def authenticate(self) -> None:
        self._service()

    def refresh_token(self) -> None:
        """Refresh ahead of use (the scheduler's proactive pass)."""
        if not self.is_configured():
            return
        self._refresh_now(self._credentials())

    def list_recent(self, limit: int = 300) -> list[MessageDTO]:
        svc = self._service()
        resp = svc.users().messages().list(userId="me", maxResults=limit).execute()
        out: list[MessageDTO] = []
        for ref in resp.get("messages", []):
            out.append(self._to_dto(svc.users().messages().get(userId="me", id=ref["id"]).execute()))
        return out

    def fetch_new(self, cursor: str | None) -> tuple[list[MessageDTO], str | None]:
        svc = self._service()
        if not cursor:
            msgs = self.list_recent(limit=25)
            profile = svc.users().getProfile(userId="me").execute()
            return msgs, str(profile.get("historyId"))
        history = svc.users().history().list(userId="me", startHistoryId=cursor).execute()
        out: list[MessageDTO] = []
        for h in history.get("history", []):
            for added in h.get("messagesAdded", []):
                out.append(self._to_dto(svc.users().messages().get(userId="me", id=added["message"]["id"]).execute()))
        return out, str(history.get("historyId", cursor))

    def get_thread(self, thread_id: str) -> ThreadDTO:
        svc = self._service()
        thread = svc.users().threads().get(userId="me", id=thread_id).execute()
        msgs = [self._to_dto(m) for m in thread.get("messages", [])]
        subject = msgs[0].subject if msgs else ""
        return ThreadDTO(provider_thread_id=thread_id, subject=subject, messages=msgs)

    def apply_label(self, provider_message_id: str, category_name: str, color: str | None = None,
                    replace_bases: set[str] | None = None) -> None:
        # color is unused on Gmail (label colors are a separate API); kept for signature parity.
        # Otto-labels zijn herkenbaar aan het Otto/-voorvoegsel: andere Otto/-labels
        # gaan van het bericht af zodat er maximaal één categorie op staat.
        svc = self._service()
        label_id = self._ensure_label(svc, f"Otto/{category_name}")
        remove: list[str] = []
        try:
            msg = svc.users().messages().get(userId="me", id=provider_message_id, format="minimal").execute()
            on_msg = set(msg.get("labelIds") or [])
            if on_msg:
                all_labels = svc.users().labels().list(userId="me").execute().get("labels", [])
                remove = [lab["id"] for lab in all_labels
                          if lab["id"] in on_msg and lab.get("name", "").startswith("Otto/")
                          and lab["id"] != label_id]
        except Exception:  # noqa: BLE001 — opruimen is best-effort; het label zetten gaat voor
            remove = []
        body = {"addLabelIds": [label_id]}
        if remove:
            body["removeLabelIds"] = remove
        svc.users().messages().modify(userId="me", id=provider_message_id, body=body).execute()

    def create_draft(self, thread_id, subject, body, in_reply_to=None, references=None,
                     signature_html=None, signature_text=None) -> str:
        svc = self._service()
        mime = EmailMessage()
        mime["Subject"] = subject
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = references or in_reply_to
        # Gmail-concepten zijn platte tekst; een org-handtekening gaat als tekst mee.
        if signature_text:
            body = f"{body}\n\n{signature_text}"
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        msg = {"raw": raw}
        if thread_id:
            msg["threadId"] = thread_id
        draft = svc.users().drafts().create(userId="me", body={"message": msg}).execute()
        return draft["id"]

    def send_user_draft(self, provider_draft_id: str) -> None:
        # Explicit, user-approved send only. Requires gmail.send in production.
        svc = self._service()
        svc.users().drafts().send(userId="me", body={"id": provider_draft_id}).execute()

    def list_folders(self) -> list[dict]:
        """User labels (Gmail's folders) as [{'id','name'}], minus Otto's own labels."""
        labels = self._service().users().labels().list(userId="me").execute().get("labels", [])
        return [{"id": lab["id"], "name": lab["name"]} for lab in labels
                if lab.get("type") == "user" and not lab["name"].startswith("Otto/")]

    def move_to_folder(self, provider_message_id: str, folder_id: str) -> None:
        # Moving = add the destination label, drop INBOX (Gmail has no true folders).
        self._service().users().messages().modify(
            userId="me", id=provider_message_id,
            body={"addLabelIds": [folder_id], "removeLabelIds": ["INBOX"]},
        ).execute()

    def ensure_folder(self, name: str) -> str | None:
        # Gmail "folder" = user label; reuse the same get-or-create helper as drafting.
        if not (name or "").strip():
            return None
        return self._ensure_label(self._service(), name.strip())

    # ── Realtime (users.watch → Pub/Sub → /api/webhooks/gmail) ────────────
    def start_watch(self) -> None:
        """Ask Gmail to push this mailbox's changes to our Pub/Sub topic.

        This is the half that was missing: the webhook, its OIDC verification and the
        Pub/Sub topic can all be configured perfectly, but Google pushes nothing until a
        mailbox is explicitly watched — so Gmail was polling-only no matter what the
        status screen claimed. Any failure degrades silently to the poll, exactly like
        the Graph subscription does.
        """
        if not current_app.config.get("GMAIL_REALTIME"):
            return
        from app.extensions import db
        from app.timefmt import parse_epoch_ms

        topic = current_app.config.get("GMAIL_PUBSUB_TOPIC")
        try:
            resp = self._service().users().watch(userId="me", body={
                "topicName": topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            }).execute()
        except Exception as exc:  # noqa: BLE001 — never let realtime setup break a sync
            current_app.logger.info("Gmail start_watch skipped: %s", exc)
            return
        # Gmail issues no subscription id, so the topic doubles as "a watch is armed" —
        # which is what the scheduler and the status screen read.
        self.account.subscription_id = topic
        self.account.watch_expiry = parse_epoch_ms(resp.get("expiration"))
        db.session.commit()

    def stop_watch(self) -> None:
        if not self.account.subscription_id:
            return
        from app.extensions import db

        try:
            self._service().users().stop(userId="me").execute()
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Gmail stop_watch skipped: %s", exc)
        self.account.subscription_id = None
        self.account.watch_expiry = None
        db.session.commit()

    def renew_watch(self) -> None:
        """A Gmail watch expires after 7 days and is extended by calling watch() again,
        so re-arming is the same operation as arming."""
        self.start_watch()

    # ── helpers ───────────────────────────────────────────────────────────
    def _ensure_label(self, svc, name: str) -> str:
        labels = svc.users().labels().list(userId="me").execute().get("labels", [])
        for lab in labels:
            if lab["name"] == name:
                return lab["id"]
        created = svc.users().labels().create(userId="me", body={"name": name}).execute()
        return created["id"]

    def _to_dto(self, msg: dict) -> MessageDTO:
        from app.timefmt import parse_epoch_ms

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        name, _, email = sender.partition("<")
        return MessageDTO(
            provider_message_id=msg["id"], thread_id=msg.get("threadId"),
            sender=name.strip().strip('"') or email.strip(">"), sender_email=email.strip(">"),
            subject=headers.get("subject", "(geen onderwerp)"), snippet=msg.get("snippet", ""),
            body=msg.get("snippet", ""), unread="UNREAD" in msg.get("labelIds", []),
            received_at=parse_epoch_ms(msg.get("internalDate")),
            list_unsubscribe=headers.get("list-unsubscribe"),
            list_unsubscribe_post=headers.get("list-unsubscribe-post"),
        )

"""Microsoft 365 / Outlook adapter via Graph — scope `Mail.ReadWrite` (no Mail.Send).

Drafts are created with `createReply` so they land in the user's Concepten folder,
with the AI reply placed ABOVE the preserved (HTML) quoted original. Categories are
written as colored Outlook master categories and MERGED onto the message (the user's
own categories are never clobbered). Realtime is delivered via Graph change-notification
subscriptions (created on connect, renewed by the beat scheduler) when GRAPH_REALTIME
is enabled; otherwise the 120s poll keeps things in sync.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

from flask import current_app

from app.providers.base import MailProvider, UserInitiatedSender
from app.providers.dto import MessageDTO, ThreadDTO
from app.providers.exceptions import ProviderAuthError, ProviderNotConfigured
from datetime import UTC

GRAPH = "https://graph.microsoft.com/v1.0"


def _timeout() -> int:
    """Seconds to wait on a single Graph call before giving up (GRAPH_TIMEOUT_SECONDS)."""
    try:
        return int(current_app.config.get("GRAPH_TIMEOUT_SECONDS") or 20)
    except (RuntimeError, TypeError, ValueError):  # no app context / bad value
        return 20

# Message properties we $select on every read. internetMessageHeaders carries the raw
# RFC 5322 headers (incl. List-Unsubscribe) — not returned by default, so it must be
# named explicitly.
# `sender` naast `from`: bij "verzonden namens"-mail is `from` soms leeg en staat de
# echte afzender alleen in `sender` — zonder dat veld komt zo'n mail zonder afzender binnen.
_MSG_SELECT = "id,conversationId,subject,bodyPreview,from,sender,isRead,body,receivedDateTime,internetMessageHeaders"


def deltalink_is_narrow(cursor: str | None) -> bool:
    """True als een opgeslagen deltaLink met een té smalle `$select` is gebouwd.

    De bug (PR #112): een baseline-cursor die met `$select=id` gedraind werd, bakt dat
    in de deltaLink. Graph negeert een nieuwe `$select` bij het volgen, dus elke latere
    fetch_new gaf id-only berichten terug — leeg onderwerp/afzender/body → als spam
    geclassificeerd. Zo'n cursor herkennen we aan een `$select=` die de inhoudsvelden
    (subject/from/body) mist. Zonder `$select` (of met de volledige set) → niet smal."""
    if not cursor:
        return False
    low = cursor.lower()
    # Graph URL-encodeert `$select` als `%24select`; dek beide vormen af.
    if "select=" not in low:
        return False
    return not ("subject" in low and "from" in low and "body" in low)
# MailboxSettings.ReadWrite is needed to create colored master categories;
# the subscription (realtime) path needs only the read scopes above.
# Mail.ReadWrite.Shared lets a user reach Exchange shared mailboxes they have
# delegate access to (no admin consent). Shared mailboxes sync by poll only —
# .Shared scopes cannot drive Graph change-notification subscriptions.
# Tasks.ReadWrite also covers Microsoft Planner (list plans/buckets + create assigned tasks).
# Calendars.ReadWrite is delegated en NIET admin-restricted (geverifieerd tegen de Graph
# permissions reference), dus het mag in de gewone koppeling: vrije momenten voorstellen in
# een concept en de afspraak zetten bij verzenden. Het staat er nu al in zodat klanten
# maar één keer toestemming hoeven te geven — een scope later toevoegen betekent iedereen
# opnieuw laten koppelen, want een bestaand refresh-token draagt hem niet.
# Mail.Send staat er bewust NIET in en hoort er nooit in te komen: "Otto verzendt nooit
# zelf" is aantoonbaar doordat de app dat recht niet heeft.
GRAPH_SCOPES = [
    "Mail.ReadWrite", "Mail.ReadWrite.Shared", "MailboxSettings.ReadWrite",
    "Tasks.ReadWrite", "Calendars.ReadWrite", "User.Read", "offline_access",
]

# User.ReadBasic.All lets the task picker resolve a colleague from the tenant directory.
# It is ADMIN-CONSENT restricted: requesting it on the normal connect throws every
# self-service user into a "need admin approval" wall (and bounces them back to onboarding).
# So it is NOT part of the default connect — only the explicit admin-consent flow requests
# it, so an admin can grant it org-wide. The assignee picker degrades to empty without it.
_DIRECTORY_SCOPE = "User.ReadBasic.All"

# MSAL auto-injects the reserved OIDC scopes (offline_access / openid / profile) and
# REJECTS them when passed explicitly ("You cannot use any scope value that is
# reserved"). Keep them in GRAPH_SCOPES for the consent UI + the eigen-app docs, but
# always hand MSAL this filtered list — for the auth-code flow AND the token refresh.
# (offline_access is still granted: MSAL re-adds it itself, so refresh tokens work.)
_RESERVED_SCOPES = frozenset({"offline_access", "openid", "profile"})
GRAPH_MSAL_SCOPES = [s for s in GRAPH_SCOPES if s not in _RESERVED_SCOPES]

# Scopes waarvan een postvak dat ze niet heeft, gewoon door moet kunnen werken.
# Een refresh-token dat vóór een uitbreiding is afgegeven, draagt de nieuwe scope niet:
# Microsoft weigert dan de vernieuwing (AADSTS65001) in plaats van hem stil weg te laten.
# Zonder opvang valt élk al gekoppeld postvak stil zodra we hier iets toevoegen. Vandaar
# de tweede poging zónder deze scopes — de mailfuncties blijven werken, en het scherm
# vraagt om opnieuw te koppelen voor wat er nieuw bij komt.
GRAPH_OPTIONAL_SCOPES = frozenset({"Calendars.ReadWrite", "OnlineMeetingTranscript.Read.All"})
# GroupMember.Read.All laat Otto M365-groepen en lidmaatschappen lezen, voor de
# handtekening-toewijzing per groep. Net als de directory-scope is hij admin-consent-only
# en zit hij dus alleen in de admin-consent-flow; zonder consent degradeert de feature
# stil (groepskiezer leeg, groepstoewijzingen matchen niet).
_GROUP_SCOPE = "GroupMember.Read.All"

# OnlineMeetingTranscript.Read.All laat Otto de transcripties lezen die de tenant zelf al
# laat maken (fase 6.6 — actiepunten eruit halen). Óók admin-consent-only, dus alleen in de
# admin-consent-flow; zonder consent degradeert de feature stil (geen transcripties → geen
# taken). In de optionele set zodat een refresh van een ouder token er niet op struikelt.
_TRANSCRIPT_SCOPE = "OnlineMeetingTranscript.Read.All"

# Superset requested only in the admin-consent flow (adds the admin-restricted directory scopes).
GRAPH_MSAL_ADMIN_SCOPES = [*GRAPH_MSAL_SCOPES, _DIRECTORY_SCOPE, _GROUP_SCOPE, _TRANSCRIPT_SCOPE]

# The 25 Outlook category presets mapped to representative RGB, so an Otto category's
# hex color can be matched to the nearest Outlook swatch (Graph only accepts presetN).
_OUTLOOK_PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("preset0", (0xE7, 0x48, 0x56)),   # Red
    ("preset1", (0xFF, 0x8C, 0x00)),   # Orange
    ("preset2", (0x8E, 0x56, 0x2E)),   # Brown
    ("preset3", (0xFD, 0xE3, 0x00)),   # Yellow
    ("preset4", (0x49, 0x82, 0x05)),   # Green
    ("preset5", (0x00, 0xB7, 0xC3)),   # Teal
    ("preset6", (0x80, 0x80, 0x00)),   # Olive
    ("preset7", (0x00, 0x78, 0xD7)),   # Blue
    ("preset8", (0x87, 0x64, 0xB8)),   # Purple
    ("preset9", (0xE3, 0x00, 0x8C)),   # Cranberry
    ("preset10", (0x69, 0x79, 0x7E)),  # Steel
    ("preset11", (0x4A, 0x54, 0x59)),  # DarkSteel
    ("preset12", (0x88, 0x88, 0x88)),  # Gray
    ("preset13", (0x55, 0x55, 0x55)),  # DarkGray
    ("preset14", (0x00, 0x00, 0x00)),  # Black
    ("preset15", (0x75, 0x0B, 0x1C)),  # DarkRed
    ("preset16", (0xCA, 0x50, 0x10)),  # DarkOrange
    ("preset17", (0x4C, 0x2B, 0x12)),  # DarkBrown
    ("preset18", (0xC1, 0x9C, 0x00)),  # DarkYellow
    ("preset19", (0x0B, 0x61, 0x0B)),  # DarkGreen
    ("preset20", (0x00, 0x6A, 0x6E)),  # DarkTeal
    ("preset21", (0x42, 0x42, 0x00)),  # DarkOlive
    ("preset22", (0x00, 0x3F, 0x7A)),  # DarkBlue
    ("preset23", (0x32, 0x14, 0x5A)),  # DarkPurple
    ("preset24", (0x6E, 0x00, 0x44)),  # DarkCranberry
]


def _text_to_html(text: str) -> str:
    """Escape Otto's plain-text draft and turn newlines into <br> (HTML-injection safe)."""
    return html.escape(text or "").replace("\n", "<br>")


def _vtt_to_text(vtt: str) -> str:
    """Een WebVTT-transcriptie naar leesbare tekst: tijdcodes en cue-nummers eruit, de
    spreker-plus-uitspraak blijft. Zo herkent app/transcripts.py sprekers en zinnen."""
    regels = []
    for regel in (vtt or "").splitlines():
        r = regel.strip()
        if not r or r == "WEBVTT" or r.isdigit() or "-->" in r:
            continue
        # Graph zet de spreker in een <v ...>-tag: "<v Jan Peeters>Oké, ik doe dat.</v>".
        m = re.match(r"<v\s+([^>]+)>(.*?)</v>", r, re.IGNORECASE)
        if m:
            regels.append(f"{m.group(1).strip()}: {m.group(2).strip()}")
        else:
            regels.append(re.sub(r"</?[^>]+>", "", r))
    return "\n".join(regels)


def _hex_to_preset(hex_color: str | None) -> str:
    """Map an Otto category hex (#RRGGBB or #RRGGBBAA) to the nearest Outlook preset."""
    if not hex_color:
        return "preset0"
    h = hex_color.strip().lstrip("#")[:6]
    if len(h) < 6:
        return "preset0"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return "preset0"
    best, best_d = "preset0", None
    for name, (pr, pg, pb) in _OUTLOOK_PRESETS:
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if best_d is None or d < best_d:
            best, best_d = name, d
    return best


def _is_consent_error(result: dict) -> bool:
    """Weigert Microsoft dit token omdat een gevraagde scope niet is toegestaan?

    AADSTS65001 is de expliciete code; de foutcodes eromheen dekken de varianten waarin
    dezelfde oorzaak anders benoemd wordt."""
    if (result or {}).get("error") in {"invalid_grant", "interaction_required", "consent_required"}:
        return True
    return "AADSTS65001" in ((result or {}).get("error_description") or "")


class OutlookProvider(MailProvider, UserInitiatedSender):
    supports_rules = True
    @property
    def _root(self) -> str:
        """Graph mailbox root. A shared mailbox is addressed via /users/{address};
        a personal mailbox via /me."""
        if getattr(self.account, "mailbox_type", "personal") == "shared" and self.account.target_address:
            return f"/users/{self.account.target_address}"
        return "/me"

    def _auth_account(self):
        """The account whose delegated token reaches this mailbox.

        A shared mailbox borrows the connecting member's personal grant (so there is
        a single delegated token to refresh); a personal mailbox uses its own token.
        """
        acct = self.account
        via_id = getattr(acct, "delegated_via_account_id", None)
        if via_id:
            from app.extensions import db
            from app.models import MailAccount

            parent = db.session.get(MailAccount, via_id)
            if parent is not None:
                return parent
        return acct

    def is_configured(self) -> bool:
        from app.connectors import graph_configured_for

        auth = self._auth_account()
        org = auth.user.organization if (auth and auth.user) else None
        return graph_configured_for(org)

    def _token(self) -> str:
        if not self.is_configured():
            raise ProviderNotConfigured("Microsoft Graph is niet geconfigureerd.")
        from app.security.crypto import decrypt

        token = decrypt(self._auth_account().access_token_enc)
        if not token:
            raise ProviderAuthError("Geen geldige Graph-token; koppel het account opnieuw.")
        return token

    def _ensure_fresh_token(self) -> None:
        """Refresh ahead of a call when the stored access token is already past expiry.

        The 401 retry in `_send` is the real safety net; this just avoids a request we
        already know will fail. No-op when the expiry is unknown (grants made before this
        was tracked) or still in the future.
        """
        from app.models.base import utcnow

        expiry = getattr(self._auth_account(), "token_expiry", None)
        if expiry is not None and expiry <= utcnow():
            try:
                self.refresh_token()
            except Exception as exc:  # noqa: BLE001 — fall through to the 401 retry below
                current_app.logger.info("proactive Graph token refresh skipped: %s", exc)

    def _send(self, method: str, path: str, payload: dict | None = None,
              extra_headers: dict | None = None, timeout: int | None = None) -> bytes:
        """One Graph request, with a single transparent token refresh on 401.

        Graph access tokens live ~1h. Rather than rely on the beat scheduler's refresh
        cadence (which is absent entirely in the no-Redis single-container deployment),
        every call self-heals: refresh proactively when the token is known-expired and —
        as a catch-all — refresh once and retry when Graph still answers 401. This is what
        keeps the background sync fetching mail beyond the first hour after connect.

        `extra_headers` carries optional request headers such as `If-Match` (used for To
        Do's optimistic-concurrency etags).

        `timeout` overschrijft de standaard Graph-timeout voor déze ene call. Bedoeld
        voor best-effort werk in een paginarender (zoals de HTML-backfill), dat snel
        moet opgeven in plaats van een gunicorn-worker de volle 20 s vast te houden.
        """
        self._ensure_fresh_token()
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._token()}"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(GRAPH + path, data=data, method=method, headers=headers)
            try:
                # ALWAYS pass a timeout: urlopen's default is None (block forever). A Graph
                # call that never answers would otherwise pin the caller indefinitely — a
                # gunicorn worker on the connect/manual-sync path, or the single background
                # poller thread, which then stops syncing every mailbox behind it.
                with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:  # noqa: S310 (trusted Graph host)
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    self.refresh_token()  # expired access token → refresh and retry once
                    continue
                raise

    def _get(self, path: str, timeout: int | None = None) -> dict:
        raw = self._send("GET", path, timeout=timeout).decode()
        return json.loads(raw) if raw else {}

    def _get_abs(self, url_or_path: str) -> dict:
        """GET a path or an absolute Graph URL (as returned by nextLink/deltaLink)."""
        path = url_or_path[len(GRAPH):] if url_or_path.startswith(GRAPH) else url_or_path
        return self._get(path)

    def _post(self, path: str, payload: dict, extra_headers: dict | None = None) -> dict:
        return self._write("POST", path, payload, extra_headers)

    def _patch(self, path: str, payload: dict, extra_headers: dict | None = None) -> dict:
        return self._write("PATCH", path, payload, extra_headers)

    def _write(self, method: str, path: str, payload: dict, extra_headers: dict | None = None) -> dict:
        raw = self._send(method, path, payload, extra_headers).decode()
        return json.loads(raw) if raw else {}

    def _delete(self, path: str, extra_headers: dict | None = None) -> None:
        self._send("DELETE", path, None, extra_headers)

    def authenticate(self) -> None:
        self._token()

    def refresh_token(self) -> None:
        from msal import ConfidentialClientApplication

        from app.connectors import microsoft_app
        from app.security.crypto import decrypt, encrypt
        from app.extensions import db

        auth = self._auth_account()
        org = auth.user.organization if (auth and auth.user) else None
        client_id, tenant_id, secret = microsoft_app(org)
        app_ = ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=secret,
        )
        refresh = decrypt(auth.refresh_token_enc)
        result = app_.acquire_token_by_refresh_token(refresh, GRAPH_MSAL_SCOPES)
        partial = False
        if "access_token" not in result and _is_consent_error(result):
            # Oudere grant: opnieuw proberen met alleen wat toen is toegestaan.
            core = [s for s in GRAPH_MSAL_SCOPES if s not in GRAPH_OPTIONAL_SCOPES]
            result = app_.acquire_token_by_refresh_token(refresh, core)
            partial = "access_token" in result
        if "access_token" in result:
            from datetime import timedelta

            from app.models.base import utcnow

            auth.access_token_enc = encrypt(result["access_token"])
            if result.get("refresh_token"):
                auth.refresh_token_enc = encrypt(result["refresh_token"])
            # Track expiry so `_ensure_fresh_token` can refresh proactively next time.
            auth.token_expiry = utcnow() + timedelta(seconds=int(result.get("expires_in") or 3600))
            auth.last_error = (
                "Koppel dit postvak opnieuw om de agendafuncties aan te zetten — "
                "de huidige toestemming dekt ze nog niet."
            ) if partial else None
            db.session.commit()
        else:
            raise ProviderAuthError(result.get("error_description", "Graph token refresh mislukt"))

    def list_recent(self, limit: int = 300) -> list[MessageDTO]:
        path: str | None = f"{self._root}/messages?$top=50&$select={_MSG_SELECT}"
        out: list[MessageDTO] = []
        while path and len(out) < limit:
            data = self._get_abs(path)
            out.extend(self._to_dto(m) for m in data.get("value", []))
            path = data.get("@odata.nextLink")
        return out[:limit]

    def fetch_new(self, cursor: str | None) -> tuple[list[MessageDTO], str | None]:
        # The deltaLink stored as `cursor` already encodes this $select, so headers keep
        # flowing on every incremental sync after the first.
        path = cursor or f"{self._root}/mailFolders/inbox/messages/delta?$select={_MSG_SELECT}"
        msgs: list[MessageDTO] = []
        while True:
            data = self._get_abs(path)
            for m in data.get("value", []):
                mid = m.get("id")
                if not mid:
                    continue
                if "@removed" in m:  # left the INBOX (deleted/archived/moved) → tell sync to drop it
                    msgs.append(MessageDTO(
                        provider_message_id=mid, thread_id=None, sender="", sender_email="",
                        subject="", snippet="", body="", removed=True))
                else:
                    # Delta levert soms een partieel item (bv. alleen een gewijzigde
                    # isRead) zónder afzender of onderwerp. Haal het bericht dan één
                    # keer volledig op, anders belandt er een lege mail in de inbox.
                    if "from" not in m and "sender" not in m:
                        full = self.get_full_message(mid)
                        if full is not None:
                            m = full
                    msgs.append(self._to_dto(m))
            nxt = data.get("@odata.nextLink")
            if nxt:
                path = nxt
                continue
            return msgs, data.get("@odata.deltaLink", cursor)

    def get_full_message(self, message_id: str) -> dict | None:
        """Eén bericht volledig ophalen (alle _MSG_SELECT-velden), of None bij een fout.
        Gebruikt als zelfherstel wanneer de delta-sync een partieel item leverde."""
        try:
            return self._get(f"{self._root}/messages/{message_id}?$select={_MSG_SELECT}")
        except Exception as exc:  # noqa: BLE001 — herstel is best-effort, de sync gaat door
            current_app.logger.info("Volledig bericht ophalen mislukt (%s): %s", message_id, exc)
            return None

    def get_message(self, message_id: str) -> MessageDTO | None:
        """DTO-variant van get_full_message — voor het herstellen van een mail die eerder
        zonder afzender/onderwerp/inhoud is opgeslagen."""
        m = self.get_full_message(message_id)
        return self._to_dto(m) if m and m.get("id") else None

    def inbox_message_ids(self) -> set[str] | None:
        """Alle id's die nu in de Inbox staan ($select=id, dus een minimale payload).

        Bij een fout None in plaats van een gedeeltelijke set: een half opgehaalde lijst
        zou de verzoening alles laten archiveren wat toevallig op de volgende pagina stond.
        """
        ids: set[str] = set()
        path: str | None = f"{self._root}/mailFolders/inbox/messages?$top=200&$select=id"
        try:
            while path:
                data = self._get_abs(path)
                ids.update(m["id"] for m in data.get("value", []) if m.get("id"))
                path = data.get("@odata.nextLink")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Inbox-inventaris mislukt: %s", exc)
            return None
        return ids

    def delta_baseline(self) -> str | None:
        """Drain the inbox delta to capture the current deltaLink WITHOUT importing the
        backlog — so the first real fetch_new only yields mail that arrives after connect.

        The drained pages are discarded (no backlog import); only the final deltaLink is
        kept. It MUST be built with the same `$select` as fetch_new: Graph bakes the
        `$select` into the deltaLink and ignores any `$select` added when following it, so
        a baseline drained with a narrower selection (e.g. `$select=id`) yields a cursor
        that makes every later fetch_new return id-only messages — no subject, sender or
        body. That fed the classifier empty mail, which then landed as spam/irrelevant."""
        path: str | None = f"{self._root}/mailFolders/inbox/messages/delta?$select={_MSG_SELECT}"
        while True:
            data = self._get_abs(path)
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return data.get("@odata.deltaLink")
            path = nxt

    def has_sent_reply_after(self, conversation_id: str, after_dt=None) -> bool:
        """True if the mailbox owner already sent a message in this conversation at/after
        `after_dt` (checked against Sent Items) — i.e. they've replied, so don't draft."""
        import urllib.parse

        from app.timefmt import parse_graph_dt

        cid = (conversation_id or "").strip()
        if not cid:
            return False
        flt = urllib.parse.quote(f"conversationId eq '{cid.replace(chr(39), chr(39) * 2)}'")
        data = self._get(
            f"{self._root}/mailFolders/sentitems/messages?$filter={flt}&$select=sentDateTime&$top=25"
        )
        for m in data.get("value", []):
            if after_dt is None:
                return True
            sent = parse_graph_dt(m.get("sentDateTime"))
            if sent and sent >= after_dt:
                return True
        return False

    def get_thread(self, thread_id: str) -> ThreadDTO:
        data = self._get(f"{self._root}/messages?$filter=conversationId eq '{thread_id}'&$select={_MSG_SELECT}")
        msgs = [self._to_dto(m) for m in data.get("value", [])]
        return ThreadDTO(provider_thread_id=thread_id, subject=msgs[0].subject if msgs else "", messages=msgs)

    def apply_label(self, provider_message_id: str, category_name: str, color: str | None = None,
                    replace_bases: set[str] | None = None) -> None:
        # Outlook "categories" are the label equivalent. Ensure a colored master
        # category exists, then apply. De eigen categorieën van de gebruiker blijven
        # altijd staan, maar ANDERE Otto-labels gaan eruit (replace_bases = de kale
        # categorienamen van de organisatie, elk nummer-voorvoegsel telt mee): een
        # bericht draagt maximaal één Otto-categorie — hercategoriseren of de
        # nummering omzetten mag nooit twee tags naast elkaar achterlaten.
        from app.outlook_labels import strip_number

        self.ensure_master_category(category_name, color)
        current = self._get(f"{self._root}/messages/{provider_message_id}?$select=categories").get("categories") or []
        bases = replace_bases or set()
        keep = [c for c in current if c != category_name and strip_number(c) not in bases]
        wanted = [*keep, category_name]
        if wanted != current:
            self._patch(f"{self._root}/messages/{provider_message_id}", {"categories": wanted})

    def retag_messages(self, old_name: str, new_name: str, color: str | None = None,
                       replace_bases: set[str] | None = None, cap: int = 300) -> int:
        """Herschrijf bestaande mail van een verouderde Otto-labelnaam naar de juiste.

        Graph kan een hoofdcategorie niet hernoemen (alleen de kleur is muteerbaar),
        dus "de oude aanpassen" = elk bericht met de oude naam her-taggen naar de
        nieuwe en daarna de oude naam uit de categorielijst halen. Best-effort en
        gecapt: een gigantische mailbox mag de aanroep niet eindeloos vasthouden."""
        import urllib.parse

        from app.outlook_labels import strip_number

        old = (old_name or "").strip()
        if not old or old == new_name:
            return 0
        self.ensure_master_category(new_name, color)
        bases = set(replace_bases or set()) | {strip_number(old), strip_number(new_name)}
        safe_old = old.replace(chr(39), chr(39) * 2)
        flt = urllib.parse.quote(f"categories/any(c:c eq '{safe_old}')")
        done = 0
        path: str | None = f"{self._root}/messages?$filter={flt}&$select=id,categories&$top=50"
        while path and done < cap:
            data = self._get_abs(path)
            for m in data.get("value", []):
                if done >= cap:
                    break
                current = m.get("categories") or []
                keep = [c for c in current if c != new_name and strip_number(c) not in bases]
                self._patch(f"{self._root}/messages/{m['id']}", {"categories": [*keep, new_name]})
                done += 1
            path = data.get("@odata.nextLink")
        if done >= cap and path:
            current_app.logger.info("retag '%s'→'%s' gecapt op %s berichten", old, new_name, cap)
        self.remove_master_category(old)
        return done

    # ── Inbox-regels (Outlook messageRules) ──────────────────────────────────
    # Regels leven in Exchange zelf, niet in Otto. Ze draaien dus ook wanneer Otto
    # stilstaat — dat is precies waarom ze naar Outlook geschreven worden in plaats van
    # in de eigen pijplijn te blijven hangen.
    def list_rules(self) -> list[dict]:
        data = self._get(f"{self._root}/mailFolders/inbox/messageRules")
        return data.get("value", [])

    def create_rule(self, payload: dict) -> str | None:
        return self._post(f"{self._root}/mailFolders/inbox/messageRules", payload).get("id")

    def update_rule(self, rule_id: str, payload: dict) -> None:
        self._patch(f"{self._root}/mailFolders/inbox/messageRules/{rule_id}", payload)

    def delete_rule(self, rule_id: str) -> None:
        self._delete(f"{self._root}/mailFolders/inbox/messageRules/{rule_id}")

    def remove_master_category(self, name: str) -> None:
        """Haal een hoofdcategorie uit Outlooks categorielijst (best-effort).

        Gebruikt na het aan- of uitzetten van de nummering, zodat je niet zowel
        "Te beantwoorden" als "1. Te beantwoorden" in je lijst overhoudt. Labels die al
        op bestaande mail staan blijven ongemoeid — die herschrijven zou elke mail in de
        mailbox moeten aanraken.
        """
        try:
            data = self._get(f"{self._root}/outlook/masterCategories?$select=id,displayName")
            for cat in data.get("value", []):
                if (cat.get("displayName") or "") == name and cat.get("id"):
                    self._delete(f"{self._root}/outlook/masterCategories/{cat['id']}")
                    return
        except Exception as exc:  # noqa: BLE001 — opruimen is nooit een fout waard
            current_app.logger.info("Hoofdcategorie opruimen overgeslagen (%s): %s", name, exc)

    def sync_master_categories(self, wanted: list, obsolete: list) -> None:
        """Werk de categorielijst in één keer bij: één GET, dan alleen de ontbrekende
        POSTs en de overbodige DELETEs. De nummering-schakelaar riep eerder per naam
        een eigen rondreis aan (remove_master_category doet een volledige lijst-GET
        per naam) — bij tientallen mailboxen × categorieën hing het request minutenlang.

        `wanted`: [(naam, kleur)] die moeten bestaan; `obsolete`: namen die weg mogen.
        Best-effort: een fout hier zet de eerstvolgende gelabelde mail alsnog recht."""
        try:
            data = self._get(f"{self._root}/outlook/masterCategories?$select=id,displayName")
        except Exception as exc:  # noqa: BLE001 — bijwerken is nooit een fout waard
            current_app.logger.info("masterCategories lezen overgeslagen: %s", exc)
            return
        existing = {(c.get("displayName") or ""): c.get("id") for c in data.get("value", [])}
        for name, color in wanted:
            if name not in existing:
                self.ensure_master_category(name, color)
        for name in obsolete:
            cat_id = existing.get(name)
            if not cat_id:
                continue
            try:
                self._delete(f"{self._root}/outlook/masterCategories/{cat_id}")
            except Exception as exc:  # noqa: BLE001
                current_app.logger.info("Hoofdcategorie opruimen overgeslagen (%s): %s", name, exc)

    def ensure_master_category(self, name: str, color: str | None = None) -> None:
        """Idempotently create a colored master category so the label renders with a
        color. Needs MailboxSettings.ReadWrite; degrades quietly (colorless) otherwise."""
        try:
            self._post(f"{self._root}/outlook/masterCategories", {"displayName": name, "color": _hex_to_preset(color)})
        except urllib.error.HTTPError as exc:
            if exc.code != 409:  # 409 = already exists (fine); 403 = scope not consented, etc.
                current_app.logger.info("masterCategory create skipped (%s): %s", exc.code, name)
        except Exception as exc:  # noqa: BLE001 — a missing color must never break categorisation
            current_app.logger.info("masterCategory create skipped: %s", exc)

    def create_draft(self, thread_id, subject, body, in_reply_to=None, references=None,
                     signature_html=None, signature_text=None, inline_images=None) -> str:
        """Create a reply DRAFT in the Concepten folder with the AI text ABOVE the
        preserved quoted original (HTML). Never sends.

        Een org-handtekening (signature_html) komt ONGE-escaped tussen de conceptekst en
        het geciteerde origineel — dit is de enige plek waar Otto zelf HTML samenstelt,
        en de handtekening is door een admin beheerd (geen mailinhoud van buiten).

        `inline_images` = lijst van {content_id, content_type, data(bytes)} die als
        verborgen inline-bijlage (Content-ID) meegaan; de handtekening-HTML verwijst er
        dan naar met <img src="cid:...">, zodat logo's/foto's niet als externe afbeelding
        geblokkeerd worden."""
        reply_html = f"<div style=\"font-family:'Verdana Pro',Verdana,sans-serif\">{_text_to_html(body)}</div>"
        if signature_html:
            reply_html += f"<div class=\"otto-signature\">{signature_html}</div>"
        if in_reply_to:
            # createReply yields a draft (with the quoted original) in Concepten.
            reply = self._post(f"{self._root}/messages/{in_reply_to}/createReply", {})
            draft_id = reply.get("id")
            quoted = (reply.get("body") or {}).get("content", "")
            if not quoted and draft_id:  # defensive: some responses omit the body
                quoted = (self._get(f"{self._root}/messages/{draft_id}?$select=body").get("body") or {}).get("content", "")
            if draft_id:
                self._patch(f"{self._root}/messages/{draft_id}", {"body": {"contentType": "HTML", "content": reply_html + quoted}})
                self._attach_inline_images(draft_id, inline_images)
            return draft_id
        created = self._post(f"{self._root}/messages", {"subject": subject, "body": {"contentType": "HTML", "content": reply_html}})
        draft_id = created.get("id")
        self._attach_inline_images(draft_id, inline_images)
        return draft_id

    def _attach_inline_images(self, draft_id: str | None, inline_images) -> None:
        """Voeg verborgen inline-bijlagen (Content-ID) toe aan een concept. Best-effort:
        een geblokkeerd/mislukt plaatje mag een concept nooit tegenhouden."""
        import base64

        if not draft_id or not inline_images:
            return
        for img in inline_images:
            data = img.get("data")
            cid = (img.get("content_id") or "").strip()
            if not data or not cid:
                continue
            try:
                self._post(f"{self._root}/messages/{draft_id}/attachments", {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": img.get("name") or f"{cid}.img",
                    "contentType": img.get("content_type") or "image/png",
                    "isInline": True,
                    "contentId": cid,
                    "contentBytes": base64.b64encode(data).decode("ascii"),
                })
            except Exception as exc:  # noqa: BLE001 — een plaatje is nooit een fout waard
                current_app.logger.info("Inline-afbeelding aan concept toevoegen overgeslagen: %s", exc)

    # Merkteken op elk concept dat Otto zelf maakt. Alleen concepten mét dit merkteken
    # worden ooit opgeruimd, zodat een zelfgeschreven concept van de gebruiker nooit
    # sneuvelt bij het vervangen van een AI-concept.
    OTTO_DRAFT_CATEGORY = "Otto-concept"

    def mark_own_draft(self, provider_draft_id: str) -> None:
        """Zet Otto's merkteken op een concept, zodat het later herkend kan worden."""
        if not provider_draft_id:
            return
        try:
            self.ensure_master_category(self.OTTO_DRAFT_CATEGORY, "#6C4CE6")
            current = self._get(
                f"{self._root}/messages/{provider_draft_id}?$select=categories"
            ).get("categories") or []
            merged = list(dict.fromkeys([*current, self.OTTO_DRAFT_CATEGORY]))
            self._patch(f"{self._root}/messages/{provider_draft_id}", {"categories": merged})
        except Exception as exc:  # noqa: BLE001 — een merkteken is nooit de moeite van een fout waard
            current_app.logger.info("Merkteken op concept overgeslagen: %s", exc)

    def find_own_drafts(self, conversation_id: str | None) -> list[str]:
        """Id's van Otto's eigen concepten in deze conversatie.

        Nodig omdat Otto het id kan kwijtraken: gaat het aanmaken goed maar het opslaan
        niet, dan staat er een concept in Outlook dat Otto niet kent — en zou de volgende
        ronde er nóg een maken. Hiermee worden ook die weesconcepten teruggevonden.
        """
        import urllib.parse

        cid = (conversation_id or "").strip()
        if not cid:
            return []
        # Enkele quotes verdubbelen: dat is hoe OData ze escapet (zelfde als in
        # has_sent_reply_after), anders breekt een conversatie-id met een quote de filter.
        safe_cid = cid.replace(chr(39), chr(39) * 2)
        flt = urllib.parse.quote(f"isDraft eq true and conversationId eq '{safe_cid}'")
        try:
            data = self._get(
                f"{self._root}/messages?$filter={flt}&$select=id,categories&$top=25"
            )
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Zoeken naar eerdere concepten overgeslagen: %s", exc)
            return []
        return [
            m["id"] for m in data.get("value", [])
            if m.get("id") and self.OTTO_DRAFT_CATEGORY in (m.get("categories") or [])
        ]

    def send_user_draft(self, provider_draft_id: str) -> None:
        # Explicit user action only. Requires Mail.Send, which Otto keeps OFF by default.
        self._post(f"{self._root}/messages/{provider_draft_id}/send", {})

    def delete_draft(self, provider_draft_id: str) -> None:
        # Remove a previous draft so a regenerated one replaces it (no duplicate concepts).
        if provider_draft_id:
            self._delete(f"{self._root}/messages/{provider_draft_id}")

    # ── Folders ──────────────────────────────────────────────────────────────
    _SYSTEM_FOLDERS = {"Inbox", "Drafts", "Outbox", "Sent Items", "Deleted Items",
                       "Junk Email", "Archive", "Conversation History"}

    # Hoe diep submappen gevolgd worden. Outlook laat diepere nesting toe, maar elk niveau
    # is een extra ronde Graph-calls; vier niveaus dekt in de praktijk alles en houdt een
    # absurd diepe boom uit een oneindige lus.
    _FOLDER_DEPTH = 4

    def list_folders(self) -> list[dict]:
        """User mail folders as [{'id','name'}], excluding the system folders.

        Submappen tellen mee. Ze werden vroeger overgeslagen — `/mailFolders` geeft alleen
        het bovenste niveau — en dat was zichtbaar op het regelscherm: een regel die naar
        een submap verplaatst kon zijn map niet terugvinden, en dan verscheen het ruwe
        Graph-id op het scherm in plaats van een naam.

        De naam van een submap krijgt zijn pad mee ("Klanten / Facturen"), zodat twee
        submappen met dezelfde naam onder verschillende ouders uit elkaar te houden zijn.
        """
        out: list[dict] = []
        self._collect_folders(f"{self._root}/mailFolders", "", out, self._FOLDER_DEPTH)
        return out

    def _collect_folders(self, url: str, prefix: str, out: list[dict], depth: int) -> None:
        data = self._get(f"{url}?$top=100&$select=id,displayName,childFolderCount")
        while True:
            for f in data.get("value", []):
                name = (f.get("displayName") or "").strip()
                if not name or (not prefix and name in self._SYSTEM_FOLDERS):
                    continue
                full = f"{prefix} / {name}" if prefix else name
                out.append({"id": f["id"], "name": full})
                if depth > 1 and (f.get("childFolderCount") or 0) > 0:
                    self._collect_folders(
                        f"{self._root}/mailFolders/{f['id']}/childFolders", full, out, depth - 1,
                    )
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return
            data = self._get_abs(nxt)

    def move_to_folder(self, provider_message_id: str, folder_id: str) -> None:
        self._post(f"{self._root}/messages/{provider_message_id}/move", {"destinationId": folder_id})

    # ── Agenda ─────────────────────────────────────────────────────────────────
    def working_hours(self):
        """De werkuren uit Outlook zelf, of None als ze niet te lezen zijn.

        Beter dan onze eigen aanname: wie om zeven uur begint of op woensdag vrij is,
        heeft dat daar al ingesteld. `MailboxSettings.ReadWrite` dekt dit al."""
        from app.scheduling import WorkingHours

        _DAGEN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}
        try:
            data = self._get(f"{self._root}/mailboxSettings/workingHours")
        except Exception:  # noqa: BLE001 — geen werkuren is geen reden om niets voor te stellen
            return None
        try:
            from datetime import time as _time

            def _klok(raw, standaard):
                # Graph geeft "09:00:00.0000000"; alleen uur en minuut doen ertoe.
                deel = str(raw or "").split(":")
                return _time(int(deel[0]), int(deel[1])) if len(deel) >= 2 else standaard

            dagen = {_DAGEN[d.lower()] for d in (data.get("daysOfWeek") or []) if d.lower() in _DAGEN}
            return WorkingHours(
                start=_klok(data.get("startTime"), None),
                end=_klok(data.get("endTime"), None),
                days=dagen or None,
            )
        except Exception:  # noqa: BLE001 — een vreemd formaat valt terug op de standaard
            return None

    # ── Afwezigheidsassistent (out-of-office) ────────────────────────────────
    def get_automatic_replies(self) -> dict | None:
        """De huidige automaticRepliesSetting van dit postvak, of None bij een fout.
        Exchange-native; `MailboxSettings.ReadWrite` dekt dit al (geen nieuwe scope)."""
        try:
            return self._get(f"{self._root}/mailboxSettings/automaticRepliesSetting")
        except Exception as exc:  # noqa: BLE001 — best-effort
            current_app.logger.info("Afwezigheidsinstelling lezen overgeslagen: %s", exc)
            return None

    def set_automatic_replies(self, setting: dict) -> None:
        """Zet de afwezigheidsassistent. `setting` is de Graph-automaticRepliesSetting.

        Otto verzendt hiermee zelf niets: Exchange stuurt het OOO-antwoord. Bij status
        'scheduled' regelt Exchange zelf de aan/uit op de geplande momenten."""
        self._patch(f"{self._root}/mailboxSettings", {"automaticRepliesSetting": setting})

    def busy_periods(self, start, end, interval_min: int = 30):
        """Bezette periodes tussen `start` en `end`, of [] als de agenda niet te lezen is.

        `getSchedule` geeft alleen vrij/bezet — geen titels, geen deelnemers. Dat is
        precies genoeg om een moment voor te stellen, en het scheelt dat er nooit
        agenda-inhoud in een prompt belandt."""
        from app.scheduling import parse_availability

        adres = self.account.target_address or self.account.email
        if not adres:
            return []
        payload = {
            "schedules": [adres],
            "startTime": {"dateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                          "timeZone": "UTC"},
            "endTime": {"dateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": "UTC"},
            "availabilityViewInterval": interval_min,
        }
        try:
            data = self._post(f"{self._root}/calendar/getSchedule", payload)
        except Exception:  # noqa: BLE001 — geen agenda-toegang → geen voorstellen, geen fout
            current_app.logger.info("[agenda] getSchedule overgeslagen voor account %s",
                                    getattr(self.account, "id", "?"))
            return []
        rijen = data.get("value") or []
        if not rijen:
            return []
        return parse_availability(rijen[0].get("availabilityView"), start, interval_min)

    def create_event(self, subject: str, start, duration_min: int = 30,
                     attendee_email: str | None = None, attendee_name: str | None = None,
                     tentative: bool = True) -> str | None:
        """Zet een agenda-item en geef het event-id terug, of None als het niet lukt.

        Wordt alleen aangeroepen als de gebruiker een klaargezette afspraak bevestigt —
        Otto boekt nooit uit zichzelf (zie app/appointment.py). `Calendars.ReadWrite` dekt
        dit; standaard `tentative` zodat het item zichtbaar "voorlopig" is tot de gebruiker
        het in Outlook bevestigt."""
        from datetime import timedelta

        if start is None:
            return None
        zone = getattr(getattr(start, "tzinfo", None), "key", None) \
            or current_app.config.get("APP_TIMEZONE") or "Europe/Brussels"
        einde = start + timedelta(minutes=max(5, int(duration_min or 30)))
        payload: dict = {
            "subject": (subject or "Afspraak")[:255],
            "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone},
            "end": {"dateTime": einde.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone},
            "showAs": "tentative" if tentative else "busy",
        }
        if attendee_email:
            payload["attendees"] = [{
                "emailAddress": {"address": attendee_email, "name": attendee_name or attendee_email},
                "type": "required",
            }]
        try:
            created = self._post(f"{self._root}/events", payload)
        except Exception:  # noqa: BLE001 — een mislukte boeking mag de app niet breken
            current_app.logger.info("[agenda] afspraak klaarzetten mislukt voor account %s",
                                    getattr(self.account, "id", "?"))
            return None
        return created.get("id")

    def ensure_folder(self, name: str) -> str | None:
        """Find the user folder called `name` (case-insensitive) or create it under the
        mailbox root, returning its id. Creation needs Mail.ReadWrite (already held)."""
        want = (name or "").strip()
        if not want:
            return None
        for f in self.list_folders():
            if (f.get("name") or "").strip().lower() == want.lower():
                return f.get("id")
        created = self._post(f"{self._root}/mailFolders", {"displayName": want})
        return created.get("id")

    # ── Follow-ups ─────────────────────────────────────────────────────────────
    def _has_inbound_reply_after(self, conversation_id: str, after_dt) -> bool:
        """True if any inbox message in this conversation arrived at/after `after_dt`."""
        cid = (conversation_id or "").strip()
        if not cid:
            return False
        import urllib.parse

        from app.timefmt import parse_graph_dt

        flt = urllib.parse.quote(f"conversationId eq '{cid.replace(chr(39), chr(39) * 2)}'")
        data = self._get(
            f"{self._root}/mailFolders/inbox/messages?$filter={flt}&$select=receivedDateTime&$top=25"
        )
        for m in data.get("value", []):
            got = parse_graph_dt(m.get("receivedDateTime"))
            if got and got >= after_dt:
                return True
        return False

    def list_recent_transcripts(self, days: int = 3, limit: int = 10) -> list[dict]:
        """Recente vergadertranscripties met hun tekst, voor het herkennen van actiepunten
        (fase 6.6). Vereist `OnlineMeetingTranscript.Read.All` — een admin-consent-scope die
        alleen via de admin-flow verleend wordt. Zonder die scope geeft Graph 403 en vallen
        we stil terug op [], zoals alle optionele agenda-/vergaderfuncties.

        Best-effort: Otto leest alleen transcripties die de tenant zélf al laat maken — geen
        opname, geen notulen-bot."""
        from datetime import datetime, timedelta

        from app.timefmt import parse_graph_dt

        try:
            data = self._get(
                f"{self._root}/onlineMeetings/getAllTranscripts?$top={min(int(limit), 50)}"
            )
        except Exception:  # noqa: BLE001 — geen scope/geen transcripties → geen actiepunten, geen fout
            current_app.logger.info("[transcripties] getAllTranscripts overgeslagen voor account %s",
                                    getattr(self.account, "id", "?"))
            return []
        lo = datetime.now(UTC) - timedelta(days=max(1, days))
        out: list[dict] = []
        for t in data.get("value", []):
            created = parse_graph_dt(t.get("createdDateTime"))
            if created is not None and created < lo:
                continue
            tid = t.get("id")
            url = t.get("transcriptContentUrl")
            if not (tid and url):
                continue
            try:
                raw = self._send("GET", f"{url}{'&' if '?' in url else '?'}$format=text/vtt").decode()
            except Exception:  # noqa: BLE001 — één onleesbare transcriptie mag de rest niet stoppen
                continue
            out.append({
                "id": tid,
                "subject": (t.get("meetingOrganizer") or {}).get("subject") or "Vergadering",
                "date": (created or datetime.now(UTC)).date(),
                "text": _vtt_to_text(raw),
            })
        return out

    def list_recent_sent(self, days: int = 3, limit: int = 40) -> list[dict]:
        """Recent verzonden mail met de volledige tekst, voor het herkennen van toezeggingen.

        Anders dan `list_awaiting_reply` gaat het hier níét om of er al geantwoord is —
        een belofte blijft een belofte, ook als de klant meteen terugschreef. En de body
        moet er echt bij: een toezegging staat vaak in de laatste zin, buiten bereik van
        de bodyPreview."""
        import urllib.parse
        from datetime import datetime, timedelta

        from app.timefmt import parse_graph_dt

        lo = datetime.now(UTC) - timedelta(days=max(1, days))
        flt = f"sentDateTime ge {lo.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        data = self._get(
            f"{self._root}/mailFolders/sentitems/messages?$filter={urllib.parse.quote(flt)}"
            f"&$orderby=sentDateTime desc&$top={min(int(limit), 100)}"
            "&$select=id,conversationId,subject,sentDateTime,toRecipients,body"
        )
        out: list[dict] = []
        for m in data.get("value", []):
            body = (m.get("body") or {})
            tekst = body.get("content") or ""
            if (body.get("contentType") or "").lower() == "html":
                from app.htmltext import html_to_text

                tekst = html_to_text(tekst)
            out.append({
                "provider_message_id": m.get("id"),
                "conversation_id": m.get("conversationId") or "",
                "subject": m.get("subject") or "",
                "sent_at": parse_graph_dt(m.get("sentDateTime")),
                "body": tekst,
            })
        return out

    def list_awaiting_reply(self, min_age_days: int, max_age_days: int, limit: int = 25) -> list[dict]:
        from datetime import datetime, timedelta

        from app.timefmt import parse_graph_dt

        now = datetime.now(UTC)
        hi = now - timedelta(days=max(0, min_age_days))       # sent at least min_age_days ago
        lo = now - timedelta(days=max(min_age_days, max_age_days))  # but not older than the window
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        flt = f"sentDateTime ge {lo.strftime(fmt)} and sentDateTime le {hi.strftime(fmt)}"
        import urllib.parse

        q = urllib.parse.quote(flt)
        data = self._get(
            f"{self._root}/mailFolders/sentitems/messages?$filter={q}"
            "&$orderby=sentDateTime desc&$top=50"
            "&$select=id,conversationId,subject,sentDateTime,toRecipients,bodyPreview"
        )
        out: list[dict] = []
        seen: set[str] = set()
        for m in data.get("value", []):
            cid = m.get("conversationId") or ""
            if cid in seen:  # only the most recent sent message per conversation
                continue
            seen.add(cid)
            sent_at = parse_graph_dt(m.get("sentDateTime"))
            if sent_at is None or self._has_inbound_reply_after(cid, sent_at):
                continue
            recips = [
                (r.get("emailAddress") or {}).get("name") or (r.get("emailAddress") or {}).get("address") or ""
                for r in (m.get("toRecipients") or [])
            ]
            out.append({
                "conversation_id": cid,
                "provider_message_id": m.get("id"),
                "subject": m.get("subject") or "",
                "to": ", ".join([r for r in recips if r]),
                "sent_at": sent_at,
                "body": m.get("bodyPreview") or "",
            })
            if len(out) >= limit:
                break
        return out

    def create_followup_draft(self, provider_message_id: str, body: str,
                              signature_html: str | None = None) -> str | None:
        """Reply-all DRAFT to a sent message, reminder text ABOVE the quoted original."""
        reply_html = f"<div style=\"font-family:'Verdana Pro',Verdana,sans-serif\">{_text_to_html(body)}</div>"
        if signature_html:
            reply_html += f"<div class=\"otto-signature\">{signature_html}</div>"
        reply = self._post(f"{self._root}/messages/{provider_message_id}/createReplyAll", {})
        draft_id = reply.get("id")
        quoted = (reply.get("body") or {}).get("content", "")
        if not quoted and draft_id:
            quoted = (self._get(f"{self._root}/messages/{draft_id}?$select=body").get("body") or {}).get("content", "")
        if draft_id:
            self._patch(f"{self._root}/messages/{draft_id}", {"body": {"contentType": "HTML", "content": reply_html + quoted}})
        return draft_id

    # ── HTML-weergave (inbox) ─────────────────────────────────────────────────
    def get_body_html(self, provider_message_id: str) -> str | None:
        """De originele HTML-body van één bericht — voor de luie backfill van mail die
        vóór de body_html-kolom gesynchroniseerd is. None bij tekst-mail of een fout.

        Korte timeout (3 s i.p.v. de standaard 20 s): dit draait ín de paginarender en
        is puur best-effort — bij een trage Graph valt het leespaneel gewoon terug op
        de tekstweergave in plaats van tot 2×20 s aan de worker te hangen."""
        try:
            raw = self._get(f"{self._root}/messages/{provider_message_id}?$select=body",
                            timeout=3).get("body") or {}
        except Exception as exc:  # noqa: BLE001 — backfill is best-effort
            current_app.logger.info("body_html backfill overgeslagen: %s", exc)
            return None
        if (raw.get("contentType") or "").lower() == "html":
            return raw.get("content") or None
        return None

    def fetch_message(self, provider_message_id: str) -> MessageDTO | None:
        """Haal één bericht volledig op (dezelfde velden als de sync). Gebruikt om
        berichten te herstellen die door de smalle-cursor-bug leeg zijn opgeslagen —
        de hercategoriseer-actie vult zo eerst de inhoud aan vóór ze opnieuw sorteert.
        None bij een fout, zodat de aanroeper dat ene bericht kan overslaan."""
        try:
            m = self._get(f"{self._root}/messages/{provider_message_id}?$select={_MSG_SELECT}")
        except Exception as exc:  # noqa: BLE001 — herstel is best-effort, per bericht
            current_app.logger.info("bericht opnieuw ophalen overgeslagen (%s): %s",
                                    provider_message_id, exc)
            return None
        return self._to_dto(m) if m.get("id") else None

    def get_inline_attachment(self, provider_message_id: str, content_id: str) -> tuple[bytes, str] | None:
        """(bytes, mimetype) van de inline-bijlage met dit contentId, of None.

        On demand per afbeelding: inline plaatjes zitten niet in de berichten-payload en
        horen niet in de database. De route erboven zet er cache-headers op, dus Graph
        wordt per plaatje hooguit af en toe geraakt."""
        import base64

        want = (content_id or "").strip().strip("<>").lower()
        if not want:
            return None
        # Eerst alleen metadata ($select zonder contentBytes): een listing mét bytes
        # downloadt élke bijlage van de mail — een nieuwsbrief met zes inline plaatjes
        # van 150 KB kost dan ~7 MB base64 per weergave, voor één plaatje van 150 KB.
        try:
            data = self._get(f"{self._root}/messages/{provider_message_id}/attachments"
                             "?$select=id,contentId,contentType")
        except Exception as exc:  # noqa: BLE001 — een kapot plaatje is nooit een fout waard
            current_app.logger.info("inline attachment ophalen overgeslagen: %s", exc)
            return None
        match = next((att for att in data.get("value", [])
                      if (att.get("contentId") or "").strip().strip("<>").lower() == want
                      and att.get("id")), None)
        if match is None:
            return None
        try:  # … en dan alleen de gevraagde bijlage zelf.
            att = self._get(f"{self._root}/messages/{provider_message_id}/attachments/{match['id']}")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("inline attachment ophalen overgeslagen: %s", exc)
            return None
        if not att.get("contentBytes"):
            return None
        try:
            return base64.b64decode(att["contentBytes"]), att.get("contentType") or "application/octet-stream"
        except Exception:  # noqa: BLE001
            return None

    # ── Microsoft 365-groepen (handtekening-toewijzing) ──────────────────────
    def list_directory_groups(self, limit: int = 500) -> list[dict] | None:
        """Alle groepen in de tenant als [{'id','name'}], voor de toewijzingskiezer.

        Vereist GroupMember.Read.All (admin-consent). None = niet beschikbaar (geen
        consent of fout) — de UI toont dan een hint in plaats van een lege lijst."""
        out: list[dict] = []
        path: str | None = "/groups?$select=id,displayName&$top=100"
        try:
            while path and len(out) < limit:
                data = self._get_abs(path)
                out.extend({"id": g["id"], "name": g.get("displayName") or g["id"]}
                           for g in data.get("value", []) if g.get("id"))
                path = data.get("@odata.nextLink")
        except Exception as exc:  # noqa: BLE001 — zonder consent hoort dit stil te degraderen
            current_app.logger.info("Graph groups list overgeslagen: %s", exc)
            return None
        out.sort(key=lambda g: g["name"].lower())
        return out[:limit]

    def my_group_ids(self) -> list[str] | None:
        """De groep-GUID's (transitief) waar dit postvak lid van is, of None bij
        ontbrekende consent/fout. Gecachet op mail_accounts.graph_group_ids."""
        ids: list[str] = []
        path: str | None = f"{self._root}/transitiveMemberOf/microsoft.graph.group?$select=id&$top=100"
        try:
            while path:
                data = self._get_abs(path)
                ids.extend(g["id"] for g in data.get("value", []) if g.get("id"))
                path = data.get("@odata.nextLink")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Graph memberOf overgeslagen: %s", exc)
            return None
        return ids

    def get_profile(self, extra_select: str | None = None) -> dict | None:
        """Het M365-profiel van de KOPPELENDE gebruiker (/me), of None bij een fout.

        Bewust /me en niet de mailbox-root: profielvelden horen bij de mens, niet bij
        een gedeeld postvak. Voedt de dagelijkse profielverversing in de sync.

        `extra_select` = extra $select-velden voor aan Graph gekoppelde handtekening-
        variabelen (bv. schema-extensions), zodat die in één call meekomen."""
        from app.graph_profile import PROFILE_SELECT

        select = extra_select or PROFILE_SELECT
        try:
            return self._get(f"/me?$select={select}")
        except Exception as exc:  # noqa: BLE001 — verversing is best-effort
            current_app.logger.info("Graph profiel ophalen overgeslagen: %s", exc)
            return None

    def get_my_photo(self, etag: str | None = None) -> tuple[bytes, str, str | None] | str | None:
        """De M365-profielfoto van de koppelende gebruiker: (bytes, content_type, etag).

        Geeft de sentinel-string ``"unchanged"`` terug wanneer `etag` nog geldig is (dan
        downloadt de sync niets opnieuw), en None bij geen foto of een fout. Vereist geen
        nieuwe scope: /me/photo valt onder de al toegekende User.Read."""
        try:
            meta = self._get("/me/photo?$select=@odata.mediaEtag")
        except Exception as exc:  # noqa: BLE001 — geen foto of geen toegang → best-effort
            current_app.logger.info("Profielfoto-metadata overgeslagen: %s", exc)
            return None
        new_etag = (meta.get("@odata.mediaEtag") or "").strip() or None
        if etag and new_etag and etag == new_etag:
            return "unchanged"
        content_type = (meta.get("@odata.mediaContentType") or "image/jpeg").strip()
        try:
            data = self._send("GET", "/me/photo/$value")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Profielfoto ophalen overgeslagen: %s", exc)
            return None
        return (data, content_type, new_etag) if data else None

    # ── Realtime (Graph change-notification subscriptions) ───────────────────
    def start_watch(self) -> None:
        """Create a subscription for new inbox mail. No-op unless realtime is enabled;
        any failure degrades silently to the 120s poll."""
        if not current_app.config.get("GRAPH_REALTIME"):
            return
        # Shared mailboxes reached via Mail.ReadWrite.Shared cannot drive Graph
        # change-notification subscriptions; they sync via the 120s poll instead.
        if getattr(self.account, "mailbox_type", "personal") == "shared":
            return
        from datetime import datetime, timedelta

        from app.extensions import db

        try:
            base = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
            expiry = datetime.now(UTC) + timedelta(minutes=4230)
            sub = self._post("/subscriptions", {
                "changeType": "created",
                "notificationUrl": f"{base}/api/webhooks/microsoft",
                "resource": "me/mailFolders('inbox')/messages",
                "expirationDateTime": expiry.isoformat().replace("+00:00", "Z"),
                "clientState": current_app.config["GRAPH_CLIENT_STATE"],
            })
            self.account.subscription_id = sub.get("id")
            self.account.watch_expiry = expiry.replace(tzinfo=None)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Graph start_watch skipped: %s", exc)

    def stop_watch(self) -> None:
        if not self.account.subscription_id:
            return
        from app.extensions import db

        try:
            self._delete(f"/subscriptions/{self.account.subscription_id}")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Graph stop_watch skipped: %s", exc)
        self.account.subscription_id = None
        self.account.watch_expiry = None
        db.session.commit()

    def renew_watch(self) -> None:
        """Extend the subscription; recreate it if Graph has dropped it. No-op when off."""
        if not current_app.config.get("GRAPH_REALTIME"):
            return
        if not self.account.subscription_id:
            self.start_watch()
            return
        from datetime import datetime, timedelta

        from app.extensions import db

        expiry = datetime.now(UTC) + timedelta(minutes=4230)
        try:
            self._patch(f"/subscriptions/{self.account.subscription_id}",
                        {"expirationDateTime": expiry.isoformat().replace("+00:00", "Z")})
            self.account.watch_expiry = expiry.replace(tzinfo=None)
            db.session.commit()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # subscription gone → recreate
                self.account.subscription_id = None
                self.start_watch()
            else:
                current_app.logger.info("Graph renew_watch skipped (%s)", exc.code)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.info("Graph renew_watch skipped: %s", exc)

    def _to_dto(self, m: dict) -> MessageDTO:
        from app.htmltext import html_to_text
        from app.timefmt import parse_graph_dt

        # Let op: Graph stuurt geselecteerde velden soms als expliciete null mee —
        # dict.get(key, default) beschermt daar níét tegen. Elk kernveld wordt daarom
        # met `or` naar een string gedwongen, anders staan er mails zonder afzender,
        # onderwerp of inhoud in de inbox (en crasht `initials` op None).
        frm = (m.get("from") or {}).get("emailAddress") or {}
        if not (frm.get("address") or frm.get("name")):
            # Sommige mail (bv. verzonden namens) heeft alleen `sender` ingevuld.
            frm = (m.get("sender") or {}).get("emailAddress") or {}
        raw = m.get("body") or {}
        preview = m.get("bodyPreview") or ""
        body = raw.get("content") or preview
        # Graph returns full HTML documents; store readable text so the AI and the
        # tekst-fallback never see <head>/<style>/@font-face noise. De originele HTML
        # gaat apart mee (body_html) voor de rijke inbox-weergave.
        body_html = None
        if (raw.get("contentType") or "").lower() == "html":
            body_html = body
            body = html_to_text(body)
        # internetMessageHeaders is a list of {name, value}; pull the List-Unsubscribe pair.
        hdrs = {(h.get("name") or "").lower(): h.get("value")
                for h in (m.get("internetMessageHeaders") or [])}
        return MessageDTO(
            provider_message_id=m["id"], thread_id=m.get("conversationId"),
            sender=frm.get("name") or "", sender_email=frm.get("address") or "",
            subject=(m.get("subject") or "").strip() or "(geen onderwerp)", snippet=preview,
            body=body or "", body_html=body_html, unread=not m.get("isRead", False),
            received_at=parse_graph_dt(m.get("receivedDateTime")),
            list_unsubscribe=hdrs.get("list-unsubscribe"),
            list_unsubscribe_post=hdrs.get("list-unsubscribe-post"),
        )

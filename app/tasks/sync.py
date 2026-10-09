"""Mailbox sync tasks. Degrade to no-ops when the account's provider is unconfigured."""
from __future__ import annotations

from flask import current_app

from app.extensions import db, release_db_connection
from app.models import MailAccount, Message, Thread
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.exceptions import ProviderAuthError
from app.tasks.enqueue import enqueue
from app.tasks.pipeline import process_incoming


def _get_or_create_thread(account: MailAccount, dto) -> Thread | None:
    """Group mail into a conversation by the provider's thread id, so the inbox can show
    one row per conversation and a full-thread detail view. NULL thread id → no grouping."""
    tid = getattr(dto, "thread_id", None)
    if not tid:
        return None
    org_id = account.organization_id or account.user.organization_id
    thread = (
        db.session.query(Thread)
        .filter_by(account_id=account.id, provider_thread_id=tid)
        .first()
    )
    if thread is None:
        thread = Thread(organization_id=org_id, account_id=account.id,
                        provider_thread_id=tid, subject=dto.subject)
        db.session.add(thread)
        db.session.flush()
    elif dto.subject and not thread.subject:
        thread.subject = dto.subject
    return thread


def _persist(account: MailAccount, dto) -> Message | None:
    from app.timefmt import reltime

    existing = (
        db.session.query(Message)
        .filter_by(account_id=account.id, provider_message_id=dto.provider_message_id)
        .first()
    )
    if existing:  # idempotent — never re-process; only backfill a missing timestamp
        if existing.received_at is None and dto.received_at is not None:
            existing.received_at = dto.received_at
            existing.time_display = reltime(dto.received_at, existing.time_display)
        # A message that returns to the INBOX (moved back) reappears in the inbox view.
        if existing.archived:
            existing.archived = False
        # Herstel een eerder half opgeslagen bericht (partiële delta): alleen LEGE
        # kernvelden aanvullen — nooit bestaande inhoud overschrijven.
        _fill_empty_fields(existing, dto)
        return None
    from app.unsubscribe import parse_list_unsubscribe

    unsub = parse_list_unsubscribe(
        getattr(dto, "list_unsubscribe", None), getattr(dto, "list_unsubscribe_post", None)
    )
    thread = _get_or_create_thread(account, dto)
    msg = Message(
        organization_id=account.organization_id or account.user.organization_id,
        user_id=account.user_id, account_id=account.id,
        thread_id=thread.id if thread else None,
        provider_message_id=dto.provider_message_id, provider_thread_id=dto.thread_id,
        sender=dto.sender, sender_email=dto.sender_email,
        initials=((dto.sender or "?")[:2] or "?").upper(), avatar_color="#6C4CE6",
        time_display=reltime(dto.received_at, "nu"),
        received_at=dto.received_at, unread=dto.unread, subject=dto.subject, snippet=dto.snippet,
        body=dto.body, body_html=getattr(dto, "body_html", None), lang=dto.lang,
        unsubscribe_url=unsub.url, unsubscribe_mailto=unsub.mailto,
        unsubscribe_oneclick=unsub.one_click,
    )
    db.session.add(msg)
    db.session.flush()
    return msg


def _fill_empty_fields(msg: Message, dto) -> bool:
    """Vul lege kernvelden van een bestaand bericht aan vanuit een verse DTO — voor
    mails die door een partieel delta-item zonder afzender/onderwerp/inhoud zijn
    opgeslagen. Overschrijft nooit bestaande inhoud. Geeft True terug als er iets
    is aangevuld."""
    if getattr(dto, "removed", False):
        return False
    changed = False
    if not msg.sender_email and dto.sender_email:
        msg.sender_email = dto.sender_email
        changed = True
    if not msg.sender and dto.sender:
        msg.sender = dto.sender
        msg.initials = ((dto.sender or "?")[:2] or "?").upper()
        changed = True
    if (not msg.subject or msg.subject == "(geen onderwerp)") \
            and dto.subject and dto.subject != "(geen onderwerp)":
        msg.subject = dto.subject
        changed = True
    if not msg.body and dto.body:
        msg.body = dto.body
        if not msg.snippet and dto.snippet:
            msg.snippet = dto.snippet
        if getattr(dto, "body_html", None):
            msg.body_html = dto.body_html
        changed = True
    return changed


def repair_message_details(account: MailAccount, limit: int = 100) -> int:
    """Haal berichten die zonder afzender of inhoud zijn opgeslagen (partiële delta-items
    van vóór het zelfherstel in fetch_new) opnieuw volledig op bij de provider en vul de
    lege velden aan. Geeft het aantal herstelde berichten terug. Best-effort per bericht."""
    if account.is_mock:
        return 0
    provider = get_provider(account)
    if not provider.is_configured() or not hasattr(provider, "get_message"):
        return 0
    broken = (
        db.session.query(Message)
        .filter(
            Message.account_id == account.id,
            Message.is_seed.is_(False),
            Message.provider_message_id.isnot(None),
            db.or_(
                Message.sender_email.is_(None), Message.sender_email == "",
                Message.body.is_(None), Message.body == "",
            ),
        )
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    repaired = 0
    for msg in broken:
        try:
            dto = provider.get_message(msg.provider_message_id)
        except Exception as exc:  # noqa: BLE001 — één onvindbaar bericht stopt de rest niet
            current_app.logger.info("[herstel] bericht %s overgeslagen: %s", msg.id, exc)
            continue
        if dto is None:
            continue
        if _fill_empty_fields(msg, dto):
            repaired += 1
    if repaired:
        db.session.commit()
    return repaired


def _archive_removed(account: MailAccount, provider_message_id: str | None) -> None:
    """A message left the mailbox INBOX (deleted/archived/moved on the server): drop it
    from Otto's inbox so the view mirrors the live mailbox. Seed rows are never touched."""
    if not provider_message_id:
        return
    (
        db.session.query(Message)
        .filter(
            Message.account_id == account.id,
            Message.provider_message_id == provider_message_id,
            Message.is_seed.is_(False),
        )
        .update({Message.archived: True}, synchronize_session=False)
    )


def _resolve_awaiting_reply(msg: Message) -> None:
    """A fresh inbound message means the other side replied — close out any earlier
    'wacht op antwoord' messages in the same conversation (their turn is done)."""
    if not msg.provider_thread_id:
        return
    (
        db.session.query(Message)
        .filter(
            Message.organization_id == msg.organization_id,
            Message.provider_thread_id == msg.provider_thread_id,
            Message.id != msg.id,
            Message.state == "awaiting_reply",
        )
        .update({Message.state: "done"}, synchronize_session=False)
    )


def _maybe_refresh_groups(account, provider) -> None:
    """Ververs ±daags de M365-directorygegevens van dit postvak: de groepscache
    (handtekening-toewijzing per groep) én de profielvelden van de eigenaar
    (functie/afdeling/locatie/telefoons — de handtekening-placeholders). Best-effort:
    zonder consent of bij een fout blijft alles staan zoals het was."""
    if account.provider != "outlook" or not hasattr(provider, "my_group_ids"):
        return
    from datetime import timedelta

    from app.models.base import utcnow

    last = account.graph_groups_synced_at
    if last is not None and last > utcnow() - timedelta(hours=24):
        return
    try:
        ids = provider.my_group_ids()
        if ids is not None:
            account.graph_group_ids = ids
        # Profielverversing meeliften op dezelfde dagelijkse klok: alleen LEGE velden
        # worden ingevuld (wat de gebruiker zelf typte wint — zie app/graph_profile.py).
        # Aan Graph gekoppelde handtekening-variabelen worden hier óók bijgewerkt, en de
        # M365-profielfoto (conditioneel op etag) gecachet voor de {{foto}}-placeholder.
        if account.user is not None and not account.is_shared and hasattr(provider, "get_profile"):
            from app.graph_profile import fill_profile_blanks, refresh_mapped_variables, select_fields_for

            try:
                me = provider.get_profile(select_fields_for(account.user.organization))
            except TypeError:  # provider (of test-fake) zonder het extra_select-argument
                me = provider.get_profile()
            if me:
                fill_profile_blanks(account.user, me)
                refresh_mapped_variables(account.user, me)
            _refresh_user_photo(account, provider)
        # Ook bij None (geen consent) de timestamp zetten: anders proberen we het bij
        # elke sync opnieuw en kost elke poll een kansloze Graph-call extra.
        account.graph_groups_synced_at = utcnow()
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 — nooit de mailsync laten struikelen
        db.session.rollback()
        current_app.logger.info("Directorycache verversen overgeslagen voor account %s: %s", account.id, exc)


def _refresh_user_photo(account, provider) -> None:
    """Cache de M365-profielfoto van de gebruiker (conditioneel op etag) voor {{foto}}.

    Best-effort en meeliftend op de dagelijkse directory-verversing: bij een ongewijzigde
    etag of geen foto gebeurt er niets. Vereist geen nieuwe scope (User.Read dekt /me/photo)."""
    if not hasattr(provider, "get_my_photo"):
        return
    user = account.user
    try:
        from app.models import UserPhoto

        existing = db.session.get(UserPhoto, user.id)
        result = provider.get_my_photo(existing.etag if existing else None)
        if result == "unchanged" or result is None:
            return
        data, content_type, etag = result
        if existing is None:
            existing = UserPhoto(user_id=user.id, organization_id=user.organization_id,
                                 content_type=content_type, data=data, etag=etag)
            db.session.add(existing)
        else:
            existing.data, existing.content_type, existing.etag = data, content_type, etag
    except Exception as exc:  # noqa: BLE001 — een foto is nooit de mailsync waard
        current_app.logger.info("Profielfoto verversen overgeslagen voor account %s: %s", account.id, exc)


def _maybe_pull_todo(account) -> None:
    """Pull Microsoft To Do for a personal Outlook mailbox, on the same cadence as mail.
    Best-effort: To Do is per-user (not mailbox-scoped) and must never stall the mail sync."""
    if account.provider != "outlook" or getattr(account, "is_shared", False):
        return
    try:
        from app.tasks.todo_sync import pull_todo

        pull_todo(account)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.info("To Do pull skipped for account %s: %s", account.id, exc)


def describe_error(exc) -> str:
    """A short, user-facing description of a provider failure — includes the HTTP status
    and the provider's own error message where available (so it's actionable, not just
    'sync failed')."""
    import json
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        msg = ""
        try:
            body = exc.read().decode("utf-8", "replace")
            msg = ((json.loads(body).get("error") or {}).get("message")) or body[:300]
        except Exception:  # noqa: BLE001
            msg = getattr(exc, "reason", "") or ""
        return f"HTTP {exc.code} — {msg}".strip()[:1000]
    return f"{type(exc).__name__}: {exc}"[:1000]


def reconcile_inbox(account: MailAccount, provider) -> int:
    """Breng Otto's beeld gelijk met wat er écht in de Inbox staat.

    De delta vertelt ons dat mail vertrokken is, en dat werkt — zolang we geen enkele
    melding missen. Maar de spiegel hangt volledig aan die stroom: raakt de cursor één
    keer kwijt (herkoppeling, fout, reset), dan begint de delta opnieuw en meldt hij
    nooit meer dat oudere mail weg is. Die blijft dan voorgoed in het overzicht staan.

    Deze verzoening kijkt daarom niet naar gebeurtenissen maar naar de stand: alles wat
    Otto nog toont maar niet meer in de Inbox zit, wordt gearchiveerd. Daarmee herstelt
    de spiegel zichzelf na elke gemiste melding in plaats van scheef te blijven staan.

    Geeft terug hoeveel berichten alsnog gearchiveerd zijn.
    """
    live = provider.inbox_message_ids()
    if live is None:
        return 0  # kon de stand niet bepalen → nooit gokken en dus nooit archiveren

    stale = (
        db.session.query(Message)
        .filter(
            Message.account_id == account.id,
            Message.archived.is_(False),
            Message.is_seed.is_(False),          # voorbeeldmail hoort niet bij de mailbox
            Message.provider_message_id.isnot(None),
        )
        .all()
    )
    gone = [m for m in stale if m.provider_message_id not in live]
    for msg in gone:
        msg.archived = True
    if gone:
        db.session.commit()
        current_app.logger.info(
            "[sync] %s bericht(en) opgeborgen in Outlook → uit het overzicht van %s",
            len(gone), account.email,
        )
    return len(gone)


def sync_account(account_id: int) -> int:
    account = db.session.get(MailAccount, account_id)
    if not account:
        return 0
    if account.is_mock:
        # A demo/seed mailbox has mock mail and no provider grant: syncing it can only
        # ever raise ProviderAuthError ("Geen geldige Graph-token"), flip the mailbox to
        # 'error' in the demo UI and fill the log with warnings. Nothing to fetch — and
        # clear the failure an earlier poll left behind, so the demo reads healthy again.
        if account.status == "error" or account.last_error:
            account.status = "connected"
            account.last_error = None
            db.session.commit()
        return 0
    try:
        provider = get_provider(account)
        if not provider.is_configured():
            return 0
        # Zelfherstel: een postvak dat vóór PR #112 gekoppeld werd, draagt mogelijk een
        # smalle-cursor (gebouwd met $select=id) — die geeft eeuwig id-only mail terug
        # die als spam belandt. Detecteer dat en herbouw de baseline één keer, zodat het
        # postvak vanzelf geneest zonder handmatig te herkoppelen.
        if account.provider == "outlook":
            from app.providers.outlook import deltalink_is_narrow

            if deltalink_is_narrow(account.sync_cursor):
                current_app.logger.info(
                    "[sync] smalle cursor hersteld voor %s — baseline opnieuw opgebouwd", account.email)
                account.sync_cursor = provider.delta_baseline()
                db.session.commit()
        # Het ophalen bij de provider kan lang duren (delta over honderden berichten).
        # Laat de verbinding los zodat hij niet al die tijd uit de pool geclaimd blijft;
        # de cursor lezen we nog vóór het loslaten uit.
        cursor_in = account.sync_cursor
        release_db_connection()
        messages, cursor = provider.fetch_new(cursor_in)
        # Persist the fetched mail FIRST and commit it, so a later failure — e.g. an
        # over-long delta cursor on Postgres — can never roll the new messages back out
        # again (the bug that made every Outlook sync silently fetch nothing).
        new_ids: list[int] = []
        for dto in messages:
            if getattr(dto, "removed", False):
                _archive_removed(account, dto.provider_message_id)
                continue
            msg = _persist(account, dto)
            if msg:
                new_ids.append(msg.id)
                _resolve_awaiting_reply(msg)
        account.last_sync = "zojuist"
        account.last_sync_at = utcnow()
        account.last_error = None  # healthy fetch → clear any prior failure
        if account.status == "error":
            account.status = "connected"
        db.session.commit()
        # Advance the cursor in its own commit, isolated so it can never undo the import.
        account.sync_cursor = cursor
        db.session.commit()
        # Kick the pipeline only once the messages are durable. Per bericht afvangen: zonder
        # Redis draait `enqueue` INLINE, dus één AI-/Graph-fout zou de overige nieuwe mail
        # overslaan — en omdat de cursor hierboven al is doorgezet, komen die berichten nooit
        # meer langs: ze blijven voor altijd zonder categorie en zonder concept. Een fout op
        # één mail mag ook niet het hele (geslaagde) ophalen als mislukt markeren.
        for msg_id in new_ids:
            try:
                enqueue(process_incoming, msg_id)
            except Exception as exc:  # noqa: BLE001 — één slecht bericht blokkeert de rest niet
                db.session.rollback()
                current_app.logger.warning(
                    "[sync] verwerken van bericht %s mislukt: %s", msg_id, describe_error(exc))
        # En breng daarna het beeld gelijk met de werkelijke Inbox, zodat mail die
        # buiten Otto om is opgeborgen ook uit het overzicht verdwijnt.
        try:
            reconcile_inbox(account, provider)
        except Exception:  # noqa: BLE001 — een mislukte verzoening mag de sync niet slopen
            db.session.rollback()
            current_app.logger.info("[sync] verzoening overgeslagen voor %s", account.email)
        _maybe_pull_todo(account)
        _maybe_refresh_groups(account, provider)
        return len(new_ids)
    except Exception as exc:  # noqa: BLE001 — one bad mailbox must never stall the others
        # A persistent failure means the inbox silently stops updating, so record it on
        # the account (visible in Integraties + the manual-sync toast) instead of letting
        # it look like 'no new mail'. Auth failures flip the mailbox to a reconnect state.
        db.session.rollback()
        detail = describe_error(exc)
        acct = db.session.get(MailAccount, account_id)
        if acct is not None:
            acct.last_error = detail
            if isinstance(exc, ProviderAuthError):
                acct.status = "error"
            db.session.commit()
        current_app.logger.warning(
            "Sync failed for account %s (%s): %s", account_id, getattr(account, "email", "?"), detail,
        )
        return 0


def initial_import(account_id: int, limit: int = 300) -> int:
    """First-connect: set the sync baseline so ONLY mail arriving AFTER connect is fetched
    and processed — no backlog import, and no concepts for already-handled old mail.

    (Was a 300-message backlog import; switched to a baseline at the user's request. The
    backlog is intentionally skipped — `fetch_new` from this cursor yields only new mail.)
    """
    account = db.session.get(MailAccount, account_id)
    if not account or account.is_mock:
        return 0
    try:
        provider = get_provider(account)
        if not provider.is_configured():
            return 0
        account.sync_cursor = provider.delta_baseline()  # capture "from now on" cursor
        account.last_sync = "zojuist"
        account.last_sync_at = utcnow()
        db.session.commit()
        return 0
    except Exception as exc:  # noqa: BLE001 — first-connect baseline is best-effort
        db.session.rollback()
        current_app.logger.info("Sync baseline skipped for account %s: %s", account_id, exc)
        return 0

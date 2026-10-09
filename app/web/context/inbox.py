"""Inbox, Outlook-weergave en de dagelijkse briefing."""
from __future__ import annotations

from flask_babel import gettext as _
from flask_login import current_user

from app.constants import TONE_LABELS, TONES

from app.web.context._base import (  # noqa: F401
    _category_count_map,
    personal_scope,  # noqa: F401 — gedeelde bouwstenen
    _account_messages,
    _categories,
    _category_counts,
    _human_minutes,
    _median,
    _messages,
    _my_shared_accounts,
    _org,
    _personal_messages,
    _visible_shared_account_ids,
    _waiting,
)

# ── HTML-weergave van het leespaneel ─────────────────────────────────────────
def _backfill_body_html(thread_msgs, cap: int = 2) -> None:
    """Luie backfill: haal de HTML-body op voor mail die vóór de body_html-kolom is
    gesynchroniseerd. Alleen de nieuwste `cap` berichten van de geopende conversatie,
    best-effort — een trage of kapotte Graph mag het leespaneel nooit blokkeren."""
    from app.extensions import db
    from app.providers import get_provider

    todo = [m for m in thread_msgs
            if m.body_html is None and m.provider_message_id and not m.is_seed
            and m.account and m.account.provider == "outlook"]
    if not todo:
        return
    try:
        changed = False
        for m in todo[-cap:]:
            provider = get_provider(m.account)
            if not provider.is_configured():
                return
            html = provider.get_body_html(m.provider_message_id)
            # Ook een tekst-mail markeren ("" i.p.v. NULL): anders proberen we hem bij
            # elke paginaweergave opnieuw. De template behandelt "" als "geen HTML".
            m.body_html = html or ""
            changed = True
        if changed:
            db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()


def _thread_html_map(thread_msgs) -> dict[int, str]:
    """{message_id: volledig srcdoc-document} voor de berichten met een HTML-body.
    Leeg dict = geen HTML beschikbaar (of nh3 ontbreekt) → de template valt terug op
    de vertrouwde tekstweergave."""
    from flask import url_for

    from app.htmlview import html_view_available, sanitize_email_html, wrap_email_document

    if not thread_msgs or not html_view_available():
        return {}
    _backfill_body_html(thread_msgs)
    out: dict[int, str] = {}
    for m in thread_msgs:
        if not m.body_html:
            continue
        cid_url = url_for("web.message_inline_image", message_id=m.id, content_id="__CID__")
        safe = sanitize_email_html(m.body_html, cid_url=cid_url)
        if safe:
            out[m.id] = wrap_email_document(safe)
    return out


def _draft_signature_name(selected) -> str | None:
    """Naam van de rijke org-handtekening die bij write-back onder dit concept komt,
    of None (dan zit de handtekening gewoon in de conceptekst zelf)."""
    if not (selected and selected.draft):
        return None
    try:
        from app.signatures import resolve_rich

        acct = selected.account
        user = acct.user if acct else None
        mb = acct.settings if (acct and acct.is_shared) else None
        resolved = resolve_rich(user, account=acct, mailbox_settings=mb)
        if resolved.html:
            return resolved.name or _("organisatie-handtekening")
    except Exception:  # noqa: BLE001 — een hint is nooit een fout waard
        pass
    return None


# ── Inbox ────────────────────────────────────────────────────────────────────
def _build_inbox_view(msgs, cats, filter_key, selected_id, *, link_endpoint, link_args=None,
                      search: str = "", unread_only: bool = False, sort: str = "new",
                      counts_map=None):
    """Shared builder for the inbox screen — used by the personal inbox and by each
    Teams (shared-mailbox) inbox. Link targets are parameterised (link_endpoint /
    link_args) so the same template keeps navigation inside the right scope.

    Category counts (rail badges) are computed before the search/unread filters so the
    numbers keep reflecting the whole mailbox — like Outlook, where a search narrows the
    list but not the folder counts."""
    link_args = link_args or {}
    # De badges komen uit SQL (counts_map), niet uit de ingeladen lijst. Daardoor blijven
    # ze kloppen óók nu die lijst begrensd is: een venster van 500 berichten mag nooit
    # betekenen dat de teller "500" zegt terwijl er 900 in die categorie zitten.
    # Zonder counts_map (Teams-weergaves) valt hij terug op tellen in Python.
    counts = counts_map if counts_map is not None else _category_counts(cats, msgs)
    totaal = counts.get(None, len(msgs)) if counts_map is not None else len(msgs)

    rail = [{"key": "alle", "name": _("Alle berichten"), "color": "#9AA4B2",
             "count": totaal, "active": filter_key == "alle"}]
    for c in cats:
        rail.append({"key": c.key, "name": c.name, "color": c.color,
                     "count": counts.get(c.id, 0), "active": filter_key == c.key})

    if filter_key == "alle":
        filtered = msgs
        list_title = _("Alle berichten")
    else:
        cat = next((c for c in cats if c.key == filter_key), None)
        filtered = [m for m in msgs if m.category and m.category.key == filter_key]
        list_title = cat.name if cat else _("Berichten")

    # Bulk-unsubscribe affordance: distinct senders in THIS view that still offer an
    # unsubscribe link. Computed on the category-scoped set (before search/quick filters)
    # so the "Schrijf alles uit" button reflects the whole category, like a folder count.
    unsub_count = len({(m.sender_email or f"m{m.id}") for m in filtered
                       if m.has_unsubscribe and m.unsubscribed_at is None})

    # ── Search + quick filters (Outlook-style) ──────────────────────────────
    # Free-text search over sender / subject / snippet / e-mail address, an
    # "only unread" toggle, and a newest/oldest sort switch.
    q = (search or "").strip().lower()
    if q:
        def _hit(m):
            return any(q in (v or "").lower() for v in
                       (m.sender, m.subject, m.snippet, m.sender_email))
        filtered = [m for m in filtered if _hit(m)]
    if unread_only:
        filtered = [m for m in filtered if m.unread]
    # Quick filters (read straight from the request so the route/context signatures stay
    # small): has a ready concept, awaiting the other side's reply, assigned to me.
    from flask import request

    f_draft = request.args.get("draft") == "1"
    f_waiting = request.args.get("waiting") == "1"
    f_mine = request.args.get("mine") == "1"
    if f_draft:
        filtered = [m for m in filtered if m.has_concept_draft]
    if f_waiting:
        filtered = [m for m in filtered if (m.state or "") == "awaiting_reply"]
    if f_mine:
        filtered = [m for m in filtered if m.assignee_user_id == current_user.id]
    if sort == "old":
        # Base order is newest-first; reversing gives oldest-first without a re-query.
        filtered = list(reversed(filtered))

    # ── Collapse to one row per conversation ────────────────────────────────
    # The newest message represents its thread; loose mail (no provider_thread_id)
    # stays its own row. thread_counts[rep_id] holds the total only when > 1.
    collapsed, thread_counts, _seen = [], {}, {}
    for m in filtered:
        key = m.provider_thread_id or f"m{m.id}"
        rep = _seen.get(key)
        if rep is not None:
            thread_counts[rep] = thread_counts.get(rep, 1) + 1
            continue
        _seen[key] = m.id
        collapsed.append(m)
    filtered = collapsed

    # Query args to carry the active search/filter across rail + row links so they
    # persist while navigating. url_for drops keys whose value is None.
    query_extra = {
        "q": search or None,
        "unread": "1" if unread_only else None,
        "sort": None if sort == "new" else sort,
        "draft": "1" if f_draft else None,
        "waiting": "1" if f_waiting else None,
        "mine": "1" if f_mine else None,
    }

    selected = next((m for m in msgs if m.id == selected_id), None)
    # The whole conversation for the reading pane (oldest-first), so the user sees the
    # full exchange, not just the selected mail.
    selected_thread = []
    if selected:
        if selected.provider_thread_id:
            thread_msgs = [m for m in msgs if m.provider_thread_id == selected.provider_thread_id]
            from datetime import datetime as _dt

            selected_thread = sorted(
                thread_msgs, key=lambda x: (x.received_at or x.created_at or _dt.min, x.id)
            )
        else:
            selected_thread = [selected]
    selected_notes = []
    if selected:
        selected_notes = [
            {"author": n.author.name if n.author else "?", "body": n.body,
             "when": n.created_at.strftime("%d/%m %H:%M") if n.created_at else _("zojuist")}
            for n in sorted(selected.notes, key=lambda n: n.id)
        ]
    return {
        "rail": rail,
        "messages": filtered,
        "thread_counts": thread_counts,
        "list_title": list_title,
        "list_subtitle": (_("%(n)s gesprek", n=len(filtered)) if len(filtered) == 1
                          else _("%(n)s gesprekken", n=len(filtered))),
        "unsub_count": unsub_count,
        "selected": selected,
        "selected_thread": selected_thread,
        "move_options": [c for c in cats if not (selected and selected.category_id == c.id)] if selected else [],
        "tones": [{"key": t, "label": TONE_LABELS[t]} for t in TONES],
        "filter_key": filter_key,
        "inbox_endpoint": link_endpoint,
        "inbox_args": link_args,
        # Route args + active search/filter state in one dict, so rail/row links keep
        # both the mailbox scope and the filters while navigating.
        "link_query": {**link_args, **query_extra},
        "selected_notes": selected_notes,
        "selected_is_shared": bool(selected and selected.account and selected.account.is_shared),
        # HTML-weergave van het leespaneel: {message_id: srcdoc}; leeg = tekstweergave.
        "thread_html": _thread_html_map(selected_thread),
        # Hint bij het concept: welke org-handtekening er server-side onder komt.
        "draft_signature_name": _draft_signature_name(selected),
        # Search / quick-filter state, echoed back for the filter bar + link building.
        "search": search or "",
        "unread_only": unread_only,
        "sort": sort,
        "f_draft": f_draft,
        "f_waiting": f_waiting,
        "f_mine": f_mine,
        "has_filters": bool(q or unread_only or f_draft or f_waiting or f_mine),
        "query_extra": query_extra,
        # Sorteren is geen filter maar een weergavekeuze, en met twee opties hoort daar
        # geen keuzelijst bij. Dit is dezelfde weergave met de volgorde omgedraaid.
        "sort_toggle_query": {**link_args, **query_extra, "sort": None if sort == "old" else "old"},
        # Alle filters weg, maar de gekozen volgorde en mailbox blijven staan.
        "clear_filters_query": {**link_args, "sort": None if sort == "new" else sort},
    }


def _digest_state(msgs):
    """(zichtbare berichten, banner) voor de digest-modus.

    Alleen het persoonlijke inboxscherm gebruikt dit. Bewust niet in `_messages()`, want
    dan zou de analyse ook maar de halve waarheid tonen — en cijfers horen over álle mail
    te gaan, ook de mail die jij nu even niet wil zien."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from flask import current_app, request

    from app import inbox_digest

    style = getattr(current_user, "style_profile", None)
    if not (style and style.digest_mode) or request.args.get("alles") == "1":
        return msgs, None
    try:
        tz = ZoneInfo(current_app.config.get("APP_TIMEZONE") or "Europe/Brussels")
    except Exception:  # noqa: BLE001 — een verkeerd ingestelde tijdzone mag de inbox niet breken
        tz = ZoneInfo("Europe/Brussels")
    now = datetime.now(tz)
    hours = inbox_digest.parse_hours(style.digest_hours)
    shown, held = inbox_digest.split(msgs, inbox_digest.last_slot(now, hours))
    if not held:
        return shown, None
    return shown, {"count": len(held), "next": inbox_digest.next_slot(now, hours).strftime("%H:%M")}


def inbox_context(
    filter_key: str,
    selected_id: int | None,
    search: str = "",
    unread_only: bool = False,
    sort: str = "new",
) -> dict:
    """The personal inbox — the user's own mail only (shared mailboxes live in Teams)."""
    # Zoeken gaat mee de query in, zodat het de héle mailbox dekt en niet alleen het
    # ingeladen venster; het Python-filter verderop past hetzelfde criterium nog eens toe
    # en is dus een no-op — bewust, zodat beide paden nooit uiteen kunnen lopen.
    msgs, digest = _digest_state(_personal_messages(search=search))
    data = _build_inbox_view(
        msgs, _categories(), filter_key, selected_id,
        link_endpoint="web.inbox",
        search=search, unread_only=unread_only, sort=sort,
        counts_map=_category_count_map(personal_scope()),
    )
    data["show_viewswitch"] = True
    data["team"] = None
    data["digest"] = digest
    return data



# ── Outlook view ─────────────────────────────────────────────────────────────
def outlook_context(folder: str, selected_id: int | None) -> dict:
    cats = _categories()
    msgs = _personal_messages()
    counts = _category_counts(cats, msgs)
    placed = [m for m in msgs if m.has_concept_draft]
    sent = [m for m in msgs if m.draft and m.draft.status == "verzonden"]

    folders = [
        {"key": "inbox", "name": _("Postvak IN"), "count": len([m for m in msgs if m.unread])},
        {"key": "concepten", "name": _("Concepten"), "count": len(placed)},
        {"key": "verzonden", "name": _("Verzonden"), "count": len(sent)},
        {"key": "archief", "name": _("Archief"), "count": 0},
    ]
    for f in folders:
        f["active"] = folder == f["key"]

    if folder == "concepten":
        source = placed
        folder_name = _("Concepten")
    elif folder == "verzonden":
        source = sent
        folder_name = _("Verzonden")
    elif folder == "archief":
        source = []
        folder_name = _("Archief")
    elif folder == "inbox":
        source = msgs
        folder_name = _("Postvak IN")
    else:
        cat = next((c for c in cats if c.key == folder), None)
        source = [m for m in msgs if m.category and m.category.key == folder]
        folder_name = cat.name if cat else _("Map")

    ol_cats = [{"key": c.key, "name": c.name, "color": c.color,
                "count": counts.get(c.id, 0), "active": folder == c.key} for c in cats]
    selected = next((m for m in msgs if m.id == selected_id), None)
    return {
        "ol_folders": folders, "ol_cats": ol_cats, "ol_list": source,
        "ol_folder_name": folder_name, "ol_selected": selected, "folder": folder,
    }



# ── Briefing ─────────────────────────────────────────────────────────────────
def _median_reply_minutes(msgs, *, days: int = 30) -> int | None:
    """Mediaan (minuten) van ontvangst → verzonden antwoord, laatste `days` dagen.

    Benadering op wat we zeker weten: het moment waarop het concept op "verzonden"
    ging (updated_at). Geen verzonden antwoorden in de periode → None, dan toont de
    briefing "—" in plaats van een verzonnen getal."""
    from datetime import timedelta

    from app.models.base import utcnow

    cutoff = utcnow() - timedelta(days=days)
    durations = []
    for m in msgs:
        d = m.draft
        if not (d and d.status == "verzonden" and d.updated_at and m.received_at):
            continue
        if d.updated_at < cutoff or d.updated_at <= m.received_at:
            continue
        durations.append((d.updated_at - m.received_at).total_seconds() / 60)
    median = _median(durations)
    return None if median is None else round(median)


def briefing_context() -> dict:
    """Alles op de briefing is echt en van vandaag — geen decor.

    'Vandaag' is de UTC-dag, dezelfde klok als received_at/created_at in de database.
    Dat scheelt hooguit een uur of twee met de wandklok van de gebruiker; voor een
    ochtendoverzicht is dat ruim goed genoeg en het houdt de queries triviaal."""
    from datetime import datetime, time as _time

    from flask_babel import format_date

    from app.constants import DRAFT_CONCEPT
    from app.extensions import db
    from app.models import FollowUp, Task
    from app.models.base import utcnow

    msgs = _messages()
    waiting = _waiting(msgs)
    day_start = datetime.combine(utcnow().date(), _time.min)

    received_today = [m for m in msgs if m.received_at and m.received_at >= day_start]
    categorized_today = sum(1 for m in received_today if m.category_id is not None)
    drafts_today = sum(1 for m in msgs
                       if m.draft and m.draft.created_at and m.draft.created_at >= day_start)

    open_followups = (
        db.session.query(FollowUp)
        .filter(FollowUp.organization_id == current_user.organization_id,
                FollowUp.user_id == current_user.id,
                FollowUp.status == DRAFT_CONCEPT)
        .order_by(FollowUp.last_sent_at.asc())
        .all()
    )

    today = utcnow().date()
    all_tasks = (
        db.session.query(Task)
        .filter(Task.user_id == current_user.id)
        .order_by(Task.due_date.asc(), Task.id.desc())
        .all()
    )
    open_tasks = [t for t in all_tasks if not t.done]
    due_tasks = [t for t in open_tasks if t.due_date and t.due_date <= today]
    # Over ÁLLE taken van vandaag, ook al afgevinkte — "Otto deed dit vandaag" mag
    # niet krimpen doordat de gebruiker werk afmaakt.
    tasks_created_today = sum(1 for t in all_tasks
                              if t.created_at and t.created_at >= day_start)

    stats = [
        {"value": str(len(waiting)), "label": _("Wachten op jouw antwoord"), "color": "#6C4CE6"},
        {"value": str(categorized_today), "label": _("Gesorteerd vandaag"), "color": "#2D9CDB"},
        {"value": _human_minutes(_median_reply_minutes(msgs)), "label": _("Reactietijd (mediaan, 30 d)"), "color": "#27AE60"},
        {"value": str(len(open_followups)), "label": _("Openstaande opvolgingen"), "color": "#F2994A"},
    ]
    return {
        "stats": stats,
        "waiting": waiting,
        "today_display": format_date(today, "EEEE d MMMM y").capitalize(),
        "otto_today": [
            {"value": categorized_today, "label": _("mails gesorteerd")},
            {"value": drafts_today, "label": _("concepten klaargezet")},
            {"value": tasks_created_today, "label": _("taken aangemaakt")},
        ],
        "due_tasks": due_tasks[:6],
        "briefing_followups": open_followups[:3],
        "today": today,
    }



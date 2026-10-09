"""Gedeelde bouwstenen voor de schermcontexten.

Hier staat wat élk scherm nodig heeft: de navigatiedefinities, de org-scoped queries
op berichten en categorieën, en `base_context` (topbar + sidebar). De schermspecifieke
modules in dit pakket bouwen hierop voort; dit bestand importeert er zelf géén van,
zodat er geen kringloop kan ontstaan.
"""
from __future__ import annotations


from flask_babel import gettext as _
from flask_babel import lazy_gettext as _l
from flask_login import current_user

from app.extensions import db
from app.models import (
    Category,
    MailAccount,
    MailboxMember,
    Message,
    Organization,
)

# Sidebar nav definitions. Each item: key, label, icon, optional section
# header, optional explicit endpoint, optional owner_only flag. Items are grouped
# under their `section` header in the sidebar (items without one render first).
# Labels/sections use lazy_gettext (_l) so they resolve to the visitor's language
# at render time, not at import time. Section headers double as grouping keys in the
# template; equal source strings yield equal lazy values within a request, so the
# sidebar still groups correctly.

USER_NAV = [
    {"key": "inbox", "label": _l("Inbox"), "icon": "inbox", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Je mail, gesorteerd en met concepten klaar")},
    {"key": "briefing", "label": _l("Dagelijkse briefing"), "icon": "briefing", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Wat vandaag op jou wacht")},
    {"key": "analyse", "label": _l("Analyse"), "icon": "analyse", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Volume, reactietijd en wat Otto bespaart")},
    {"key": "tasks", "label": _l("Taken"), "icon": "tasks", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Je takenlijst, gesynct met To Do")},
    {"key": "follow_ups", "label": _l("Opvolgingen"), "icon": "follow_ups", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Verzonden mail die op antwoord wacht")},
    {"key": "appointments", "label": _l("Afspraken"), "icon": "tasks", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Bevestigde momenten, klaar voor je agenda")},
    {"key": "teams", "label": _l("Teams"), "icon": "teams", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Gedeelde mailboxen voor je team")},
    {"key": "crm", "label": _l("CRM"), "icon": "integrations", "section": _l("Werk"), "section_key": "werk",
     "desc": _l("Contacten, bedrijven en projecten")},
    {"key": "categories", "label": _l("Categorieën"), "icon": "categories", "section": _l("Instellen"), "section_key": "instellen",
     "desc": _l("Waar elke soort mail belandt")},
    {"key": "rules", "label": _l("Regels"), "icon": "rules", "section": _l("Instellen"), "section_key": "instellen",
     "desc": _l("Outlook-regels, ook als Otto uitstaat")},
    {"key": "integrations", "label": _l("Integraties"), "icon": "integrations", "section": _l("Instellen"), "section_key": "instellen",
     "desc": _l("Mailbox, CRM en andere koppelingen")},
    {"key": "settings", "label": _l("Instellingen"), "icon": "settings", "section": _l("Instellen"), "section_key": "instellen",
     "desc": _l("Profiel, schrijfstijl en voorkeuren")},
]
ADMIN_NAV = [
    {"key": "overzicht", "label": _l("Overzicht"), "icon": "overzicht"},
    {"key": "users", "label": _l("Gebruikers"), "icon": "users", "section": _l("Team"), "section_key": "team",
     "desc": _l("Collega's, rollen en uitnodigingen")},
    {"key": "roles", "label": _l("Rollen & rechten"), "icon": "roles", "section": _l("Team"), "section_key": "team",
     "desc": _l("Wie wat mag zien en doen")},
    {"key": "sso", "label": _l("Aanmelden & beveiliging"), "icon": "sso", "section": _l("Beveiliging & koppelingen"), "section_key": "beveiliging",
     "desc": _l("SSO, domeinen en toegang")},
    {"key": "providers", "label": _l("E-mailkoppeling"), "icon": "providers", "section": _l("Beveiliging & koppelingen"), "section_key": "beveiliging",
     "desc": _l("Microsoft 365- of Gmail-app")},
    {"key": "ai", "label": _l("AI & model"), "icon": "ai", "section": _l("AI & sortering"), "section_key": "ai",
     "desc": _l("Model, eigen sleutel en budget")},
    {"key": "orgcats", "label": _l("Categorie-templates"), "icon": "orgcats", "section": _l("AI & sortering"), "section_key": "ai",
     "desc": _l("Categorieën voor de hele organisatie")},
    {"key": "signatures", "label": _l("Handtekeningen"), "icon": "signatures", "section": _l("AI & sortering"), "section_key": "ai",
     "desc": _l("Centrale handtekeningen en variabelen")},
    {"key": "autoreplies", "label": _l("Afwezigheid"), "icon": "autoreply", "section": _l("AI & sortering"), "section_key": "ai",
     "desc": _l("Automatische out-of-office-antwoorden")},
    {"key": "knowledge", "label": _l("Bedrijfskennis"), "icon": "knowledge", "section": _l("AI & sortering"), "section_key": "ai",
     "desc": _l("Documenten die concepten voeden")},
    {"key": "audit", "label": _l("Audit log"), "icon": "audit", "section": _l("Account"), "section_key": "account",
     "desc": _l("Wie deed wat, en wanneer")},
]

# Account en hulp horen niet in de schermlijst thuis: het zijn geen plekken waar je
# wérkt, maar dingen die je af en toe nodig hebt. Ze staan onderaan de icoonstrip,
# achter je eigen avatar, zodat de zijbalk alleen schermen toont.
ACCOUNT_NAV = [
    {"key": "rondleiding", "label": _l("Rondleiding"), "icon": "rondleiding",
     "endpoint": "web.rondleiding", "user_only": True},
    {"key": "docs", "label": _l("Documentatie"), "icon": "docs", "endpoint": "docs.home",
     "external": True},
    {"key": "logout", "label": _l("Afmelden"), "icon": "logout", "endpoint": "auth.logout"},
]


def js_i18n() -> dict[str, str]:
    """Dutch→localized map for strings shown by client-side JS (otto.js, modals.js and
    inline handlers), which Jinja can't reach. Injected once as window.OTTO_I18N; JS
    looks a string up with T("<dutch source>"). Runtime-variable parts use a {token}
    the JS replaces after translation. Literal _() calls here keep them pybabel-extractable."""
    return {
        # otto.js
        "Deze taak verwijderen?": _("Deze taak verwijderen?"),
        "Verwijderen mislukt.": _("Verwijderen mislukt."),
        "Verzenden mislukt.": _("Verzenden mislukt."),
        "Herindelen mislukt.": _("Herindelen mislukt."),
        "Opslaan mislukt.": _("Opslaan mislukt."),
        "Postvak loskoppelen? Otto verwijdert de toegang en alle gecachte mails en concepten uit Otto. Je Outlook zelf blijft ongewijzigd.":
            _("Postvak loskoppelen? Otto verwijdert de toegang en alle gecachte mails en concepten uit Otto. Je Outlook zelf blijft ongewijzigd."),
        "Uitschrijven bij deze afzender? We verwerken het uitschrijfverzoek en houden er een log van bij.":
            _("Uitschrijven bij deze afzender? We verwerken het uitschrijfverzoek en houden er een log van bij."),
        "Otto schrijft je uit bij alle afzenders in deze weergave die automatisch uitschrijven ondersteunen, en houdt er een log van bij. Doorgaan?":
            _("Otto schrijft je uit bij alle afzenders in deze weergave die automatisch uitschrijven ondersteunen, en houdt er een log van bij. Doorgaan?"),
        "Dit gedeelde postvak verlaten?": _("Dit gedeelde postvak verlaten?"),
        "Deze kennisbron verwijderen?": _("Deze kennisbron verwijderen?"),
        "Dit project verwijderen? De gekoppelde mail zelf blijft bestaan.":
            _("Dit project verwijderen? De gekoppelde mail zelf blijft bestaan."),
        "Alle mail in je inbox opnieuw laten sorteren? Verkeerd binnengekomen berichten worden hersteld en opnieuw gecategoriseerd.":
            _("Alle mail in je inbox opnieuw laten sorteren? Verkeerd binnengekomen berichten worden hersteld en opnieuw gecategoriseerd."),
        "Bezig…": _("Bezig…"),
        # modals.js
        "Uitnodigen mislukt.": _("Uitnodigen mislukt."),
        "Test-modus (geen mailer) — kopieer de uitnodigingslink:": _("Test-modus (geen mailer) — kopieer de uitnodigingslink:"),
        "Uitnodiging opnieuw verstuurd.": _("Uitnodiging opnieuw verstuurd."),
        "Versturen mislukt.": _("Versturen mislukt."),
        "Uitnodiging intrekken? De gemailde link werkt dan niet meer.": _("Uitnodiging intrekken? De gemailde link werkt dan niet meer."),
        "Dit lid verwijderen uit het postvak?": _("Dit lid verwijderen uit het postvak?"),
        # inline template handlers
        "Definitief verwijderen? Dit kan niet ongedaan gemaakt worden.": _("Definitief verwijderen? Dit kan niet ongedaan gemaakt worden."),
        "Omgeving naar de prullenbak verplaatsen?": _("Omgeving naar de prullenbak verplaatsen?"),
        "Foutenlog wissen?": _("Foutenlog wissen?"),
        "De slug klopt niet — geannuleerd.": _("De slug klopt niet — geannuleerd."),
        # inline handlers with a runtime {token}
        'Typ exact "{name}" om te bevestigen.': _('Typ exact "{name}" om te bevestigen.'),
        'Weet je zeker dat je de omgeving van "{name}" wilt verwijderen? Iedereen verliest toegang.':
            _('Weet je zeker dat je de omgeving van "{name}" wilt verwijderen? Iedereen verliest toegang.'),
        'Definitief verwijderen van "{name}" kan NIET ongedaan worden gemaakt. Typ exact de slug om te bevestigen: {slug}':
            _('Definitief verwijderen van "{name}" kan NIET ongedaan worden gemaakt. Typ exact de slug om te bevestigen: {slug}'),
    }



def _org() -> Organization:
    return db.session.get(Organization, current_user.organization_id)


def _categories() -> list[Category]:
    return (
        db.session.query(Category)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(Category.position, Category.id)
        .all()
    )


def _visible_shared_account_ids() -> set[int]:
    """Shared-mailbox account ids the current user is an assigned member of."""
    rows = (
        db.session.query(MailboxMember.mail_account_id)
        .filter_by(user_id=current_user.id)
        .all()
    )
    return {r[0] for r in rows}


# Elke berichtenrij leest categorie, concept, mailbox en toegewezene uit. Zonder dit
# haalt SQLAlchemy die per bericht apart op — bij tien mails al tien losse queries op
# `drafts`, en bij een echte mailbox honderden. selectinload haalt ze in één keer per
# relatie op, ongeacht hoeveel berichten er zijn.
# Hoeveel berichten een lijstweergave hoogstens inlaadt. De tellingen komen apart uit
# SQL (zie _category_count_map), dus dit plafond maakt geen enkel getal verkeerd — het
# begrenst alleen hoe ver de zichtbare lijst terugkijkt.
LIST_WINDOW = 500


def _with_row_relations(query):
    from sqlalchemy.orm import defer, selectinload

    return query.options(
        selectinload(Message.category),
        selectinload(Message.draft),
        selectinload(Message.account),
        selectinload(Message.assignee),
        # De lijst toont afzender/onderwerp/snippet — nooit de volledige tekst. Die
        # kolommen meeslepen betekende de hele mailinhoud van elk bericht door Python
        # halen bij élke inbox-render. Het leespaneel laadt de body van het GESELECTEERDE
        # bericht alsnog, met één extra query voor die ene rij.
        defer(Message.body),
        defer(Message.body_html),
    )


def _search_clause(search: str | None):
    """Vrije tekst over afzender/onderwerp/snippet/adres — als SQL, zodat zoeken de héle
    mailbox dekt en niet alleen het ingeladen venster."""
    q = (search or "").strip()
    if not q:
        return None
    like = f"%{q}%"
    return db.or_(
        Message.sender.ilike(like), Message.subject.ilike(like),
        Message.snippet.ilike(like), Message.sender_email.ilike(like),
    )


def _category_count_map(scope_clause) -> dict:
    """{category_id: aantal} in SQL, plus de sleutel None voor het totaal.

    Voorheen werd hiervoor de volledige berichtenlijst in Python geteld; daardoor kon de
    lijst niet begrensd worden zonder de badges te vervalsen. Nu staan ze los."""
    rijen = (
        db.session.query(Message.category_id, db.func.count(Message.id))
        .outerjoin(MailAccount, Message.account_id == MailAccount.id)
        .filter(Message.archived.is_(False), scope_clause)
        .group_by(Message.category_id)
        .all()
    )
    counts = {cid: n for cid, n in rijen}
    counts[None] = sum(n for _cid, n in rijen)
    return counts


def _messages() -> list[Message]:
    """Org mail the current user may see: personal mailboxes plus the shared mailboxes
    they're a member of. Shared-mailbox mail stays hidden from non-members."""
    visible_shared = _visible_shared_account_ids()
    # Personal mailbox mail is private to its owner; legacy rows (mailbox_type IS NULL)
    # and account-less seed mail count as personal too. Shared-mailbox mail is visible
    # only to its assigned members. Mirrors Message.visible_to() for single lookups.
    personal = db.or_(
        # account-less (seed) mail → scoped by the message's own owner
        db.and_(MailAccount.id.is_(None), Message.user_id == current_user.id),
        # personal mailbox → scoped by the account owner (robust to an un-backfilled
        # Message.user_id); legacy rows with NULL mailbox_type count as personal too
        db.and_(
            MailAccount.id.isnot(None),
            db.or_(MailAccount.mailbox_type.is_(None), MailAccount.mailbox_type != "shared"),
            MailAccount.user_id == current_user.id,
        ),
    )
    clauses = [personal]
    if visible_shared:
        clauses.append(MailAccount.id.in_(visible_shared))
    return _with_row_relations(
        db.session.query(Message)
        .outerjoin(MailAccount, Message.account_id == MailAccount.id)
        .filter(
            Message.organization_id == current_user.organization_id,
            Message.archived.is_(False),
            db.or_(*clauses),
        )
        # Newest mail first, by the actual received time (created_at as a fallback for
        # seed/account-less rows that never got a received_at). Previously ordered by
        # ascending id, which buried freshly-synced Outlook mail at the bottom of the
        # list — making it look like new mail "wasn't showing up". id breaks ties.
        .order_by(db.func.coalesce(Message.received_at, Message.created_at).desc(), Message.id.desc())
    ).all()


def _own_mail_clause():
    """Filter dat alleen de EIGEN mail van de ingelogde gebruiker doorlaat.

    Eén definitie, gedeeld door de lijst en de badge-telling. Die twee mochten nooit uit
    elkaar lopen: toen de lijstquery deze scoping miste, zag elke collega de persoonlijke
    mail van iedereen terwijl `visible_to()` wél correct was."""
    return db.or_(
        # account-less (seed) mail → scoped by the message's own owner
        db.and_(MailAccount.id.is_(None), Message.user_id == current_user.id),
        # personal mailbox → scoped by the account owner (NULL mailbox_type = personal)
        db.and_(
            MailAccount.id.isnot(None),
            db.or_(MailAccount.mailbox_type.is_(None), MailAccount.mailbox_type != "shared"),
            MailAccount.user_id == current_user.id,
        ),
    )


def _waiting_count() -> int:
    """Aantal eigen mails dat op antwoord wacht én een klaargezet concept heeft.

    Als COUNT in SQL, want dit draait bij ELKE paginaweergave (de badge in de topbalk).
    Voorheen werd daarvoor de volledige berichtenlijst ingeladen — met vier
    selectinload-relaties erbij — alleen om er de lengte van te tellen."""
    from app.models import Category, Draft

    return (
        db.session.query(db.func.count(Message.id))
        .outerjoin(MailAccount, Message.account_id == MailAccount.id)
        .join(Category, Message.category_id == Category.id)
        .join(Draft, Draft.message_id == Message.id)
        .filter(
            Message.organization_id == current_user.organization_id,
            Message.archived.is_(False),
            Category.key == "te_beantwoorden",
            Draft.status == "concept",
            _own_mail_clause(),
        )
        .scalar()
    ) or 0


def personal_scope():
    """Het volledige filter voor de eigen mail van de ingelogde gebruiker."""
    return db.and_(
        Message.organization_id == current_user.organization_id,
        Message.archived.is_(False),
        _own_mail_clause(),
    )


def _personal_messages(limit: int | None = LIST_WINDOW, search: str | None = None) -> list[Message]:
    """The current user's OWN mail only. Shared-mailbox mail is excluded — it lives
    under the Teams environment now, consulted per mailbox (not mixed into the inbox)."""
    # De eigenaars-clausule MOET hier staan. Zonder haar filterde deze query alleen op
    # organisatie: elke collega zag daardoor de persoonlijke mail van iedereen in het
    # bedrijf (lijst én leespaneel), terwijl de docstring "OWN mail only" belooft en
    # Message.visible_to() dat ook afdwingt. Zelfde scoping als _messages() hierboven.
    q = (
        db.session.query(Message)
        .outerjoin(MailAccount, Message.account_id == MailAccount.id)
        .filter(personal_scope())
    )
    if (zoek := _search_clause(search)) is not None:
        q = q.filter(zoek)
    # Newest-first by real received time (created_at fallback for seed rows). The inbox
    # view assumes this base order and only reverses it for "oudste eerst"; ordering by
    # ascending id here inverted both sort options.
    q = q.order_by(db.func.coalesce(Message.received_at, Message.created_at).desc(),
                   Message.id.desc())
    if limit:
        q = q.limit(limit)
    return _with_row_relations(q).all()


def _account_messages(account_id: int) -> list[Message]:
    """All (non-archived) mail for one mailbox. The caller must verify the current
    user may see this account (membership) — see _resolve_team_mailbox."""
    return _with_row_relations(
        db.session.query(Message)
        .filter(
            Message.organization_id == current_user.organization_id,
            Message.account_id == account_id,
            Message.archived.is_(False),
        )
        .order_by(db.func.coalesce(Message.received_at, Message.created_at).desc(), Message.id.desc())
    ).all()


def _my_shared_accounts() -> list[MailAccount]:
    """The shared mailboxes the current user may see (for the inbox filter chips)."""
    ids = _visible_shared_account_ids()
    if not ids:
        return []
    return (
        db.session.query(MailAccount)
        .filter(MailAccount.id.in_(ids))
        .order_by(MailAccount.id)
        .all()
    )


def _median(values: list[float]) -> float | None:
    """Mediaan — bij een even aantal het gemiddelde van de twee middelste waarden.
    Gedeeld door team-analytics, briefing en het Analyse-bord, zodat 'reactietijd'
    overal exact hetzelfde betekent."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _human_minutes(minutes: float | None) -> str:
    """'m 47' → '47 min', '≈ 3 u 12' → leesbaar, zonder valse precisie."""
    if minutes is None:
        return "—"
    if minutes < 60:
        return _("%(n)d min", n=round(minutes))
    hours = minutes / 60
    if hours < 24:
        return _("%(h)d u %(m)02d", h=int(hours), m=round((hours - int(hours)) * 60))
    return _("%(d)d d", d=round(hours / 24))


def _category_counts(cats, msgs) -> dict[int, int]:
    counts = {c.id: 0 for c in cats}
    for m in msgs:
        if m.category_id in counts:
            counts[m.category_id] += 1
    return counts


def _waiting(msgs) -> list[Message]:
    return [m for m in msgs if m.category and m.category.key == "te_beantwoorden" and m.has_concept_draft]


def provider_name() -> str | None:
    acct = current_user.mail_accounts[0] if current_user.mail_accounts else None
    if not acct:
        return None
    return {"gmail": "Gmail", "outlook": "Outlook", "imap": "IMAP"}.get(acct.provider, acct.provider)



# ── Shared layout context (top bar + sidebar) ────────────────────────────────
def base_context(workspace: str, active: str) -> dict:
    is_user = workspace == "user"
    # The inbox badge counts the user's OWN waiting mail (shared mailboxes live
    # under Teams now), so it matches what the inbox actually shows.
    waiting_count = _waiting_count()
    from flask import url_for

    is_owner = bool(
        (current_user.role and current_user.role.is_owner)
        or (current_user.organization and current_user.organization.owner_user_id == current_user.id)
    )
    nav_def = USER_NAV if is_user else ADMIN_NAV
    nav = []
    for item in nav_def:
        if item.get("owner_only") and not is_owner:
            continue
        key = item["key"]
        endpoint = item.get("endpoint")
        if endpoint:
            href = url_for(endpoint)
        elif is_user:
            href = url_for(f"web.{key}")
        else:
            href = url_for("web.admin_screen", screen=key)
        nav.append({
            "key": key, "label": item["label"], "icon": item.get("icon", "settings"),
            "href": href, "section": item.get("section"),
            "section_key": item.get("section_key"), "desc": item.get("desc"),
            "active": active == key,
            "badge": waiting_count if (is_user and key == "inbox" and waiting_count) else 0,
        })

    # ── Werkomgevingen (de icoonstrip) ────────────────────────────────────
    # Postvak en Beheer waren tot nu toe een klein knopje in de topbar, waardoor het
    # voelde als één lange lijst in plaats van twee contexten. Als strip staan ze
    # permanent in beeld en verdwijnt de wissel uit de schermlijst.
    from app.security.rbac import is_platform_owner

    workspaces = [
        {"key": "user", "label": _("Postvak"), "icon": "workspace_user",
         "href": url_for("web.inbox"), "active": is_user},
        {"key": "admin", "label": _("Beheer"), "icon": "workspace_admin",
         "href": url_for("web.admin_screen", screen="overzicht"), "active": not is_user},
    ]
    if is_platform_owner():
        workspaces.append({"key": "owner", "label": _("Platform"), "icon": "workspace_owner",
                           "href": url_for("owner.dashboard"), "active": False})

    account = []
    for item in ACCOUNT_NAV:
        if item.get("user_only") and not is_user:
            continue
        account.append({
            "key": item["key"], "label": item["label"], "icon": item["icon"],
            "href": url_for(item["endpoint"]), "external": bool(item.get("external")),
            "active": active == item["key"],
        })

    # ── Topbar-menu's ─────────────────────────────────────────────────────
    # Eén navbar met per werkomgeving TWEE dropdown-knoppen; de secties van de
    # zijbalk worden groepskoppen ín het menu. Items zonder sectie (Overzicht)
    # blijven een gewone link vóór de knoppen. De zijbalk-markup blijft bestaan
    # voor de mobiele off-canvas lade (die toont `nav` zoals vanouds).
    menu_of_section = {"werk": "werk", "instellen": "instellen",
                       "team": "organisatie", "beveiliging": "organisatie",
                       "ai": "instellingen", "account": "instellingen"}
    menu_labels = {"werk": _("Werk"), "instellen": _("Instellen"),
                   "organisatie": _("Organisatie"), "instellingen": _("Instellingen")}
    nav_inline, nav_menus = [], []
    for n in nav:
        if n["key"] == "teams":
            continue  # Teams heeft een eigen topbar-knop met subnavigatie (zie teams_nav)
        mkey = menu_of_section.get(n.get("section_key") or "")
        if not mkey:
            nav_inline.append(n)
            continue
        menu = next((m for m in nav_menus if m["key"] == mkey), None)
        if menu is None:
            menu = {"key": mkey, "label": menu_labels.get(mkey, mkey),
                    "sections": [], "active": False, "badge": 0}
            nav_menus.append(menu)
        sec = next((s for s in menu["sections"] if s["key"] == n["section_key"]), None)
        if sec is None:
            sec = {"key": n["section_key"], "title": n.get("section"), "items": []}
            menu["sections"].append(sec)
        sec["items"].append(n)
        menu["active"] = menu["active"] or n["active"]
        menu["badge"] += int(n.get("badge") or 0)

    # ── Teams: eigen topbar-knop met subnavigatie ─────────────────────────
    # Een dropdown per gedeelde mailbox (Inbox / Analyse / Instellingen).
    teams_nav = None
    if is_user:
        mailboxes = []
        for acct in _my_shared_accounts():
            st = acct.settings
            label = (getattr(st, "display_name", None) if st else None) or acct.target_address or acct.email
            mailboxes.append({
                "id": acct.id, "label": label,
                "inbox": url_for("web.team_inbox", account_id=acct.id),
                "analyse": url_for("web.team_analytics", account_id=acct.id),
                "settings": url_for("web.team_settings", account_id=acct.id),
            })
        teams_nav = {
            "entitled": True,
            "active": active == "teams",
            "mailboxes": mailboxes,
            "overview": url_for("web.teams"),
            "integrations": url_for("web.integrations"),
        }

    return {
        "workspace": workspace,
        "is_user": is_user,
        "is_admin": not is_user,
        "active_screen": active,
        "nav": nav,
        "nav_inline": nav_inline,
        "nav_menus": nav_menus,
        "teams_nav": teams_nav,
        "workspaces": workspaces,
        "account_nav": account,
        "provider_name": provider_name(),
        "my_role": current_user.role_name,
        "me": current_user,
    }



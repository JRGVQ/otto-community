"""Tenant provisioning — the scaffolding every company (organisation) gets.

Shared by the demo seed and by per-company registration, so a freshly registered
company lands in a fully working, isolated environment: its own categories,
settings, integrations, provider/AI config and an active access row.
"""
from __future__ import annotations

import re
import secrets

from flask_babel import gettext as _

from app.constants import DEFAULT_TONE, PERMISSIONS, ROLE_MATRIX, ROLES, TONES
from app.extensions import db
from app.models import (
    AiConfig,
    AuditLog,
    Category,
    Draft,
    DraftToneVariant,
    GraphScope,
    Integration,
    License,
    Message,
    Organization,
    Permission,
    Role,
    RolePermission,
    Setting,
    SsoConfig,
    StyleProfile,
    Subcategory,
    SystemStatus,
    Task,
    User,
)
from app.models.user import initials_for
from app.seed import demo_data as D


# ── Permissions (global catalogue) + roles (per-tenant) ──────────────────────
def ensure_permissions() -> dict[str, Permission]:
    """The global permission *catalogue* — the set of permissions that can exist,
    shared platform-wide. Tenants never edit the catalogue; which role HAS which
    permission is per-org (see provision_roles)."""
    if db.session.query(Permission).count() == 0:
        for key, label in PERMISSIONS:
            db.session.add(Permission(key=key, label=label))
        db.session.flush()
    return {p.key: p for p in db.session.query(Permission).all()}


def provision_roles(org: Organization) -> dict[str, Role]:
    """Create THIS organisation's own role set + permission matrix. Per-org by design:
    a tenant editing its roles/permissions can never affect another tenant."""
    perms = ensure_permissions()
    roles: dict[str, Role] = {}
    for name in ROLES:
        role = Role(organization_id=org.id, name=name, is_owner=(name == "Eigenaar"))
        db.session.add(role)
        db.session.flush()
        for key, perm in perms.items():
            db.session.add(RolePermission(role_id=role.id, permission_id=perm.id, allowed=bool(ROLE_MATRIX[name][key])))
        roles[name] = role
    return roles


def first_name(name: str) -> str:
    """The part of a name to greet someone by. Never raises: `name.split()[0]` blows up
    on a name that is only whitespace, which is an IndexError in the middle of signup."""
    parts = (name or "").split()
    return parts[0] if parts else (name or "").strip() or "?"


def org_role(org: Organization, name: str) -> Role | None:
    """Fetch one of an org's roles by name (roles are per-org)."""
    return db.session.query(Role).filter_by(organization_id=org.id, name=name).first()


# ── Slugs ────────────────────────────────────────────────────────────────────
SLUG_MAX_LEN = 64        # Organization.slug is String(64)
_SLUG_SUFFIX_ROOM = 9    # "-" + up to 8 characters of counter/random suffix


def slugify(name: str) -> str:
    """A slug base that always fits the column, suffix included.

    A company name may be twice as long as the slug column (the registration form
    accepts 128 characters). Without the trim the INSERT dies on Postgres with
    "value too long for type character varying(64)" — a 500 on the registration
    screen — while SQLite silently stores the oversized value, so it never shows up
    in development or in the test suite.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[: SLUG_MAX_LEN - _SLUG_SUFFIX_ROOM].rstrip("-") or "bedrijf"


def unique_slug(name: str, *, random_suffix: bool = False) -> str:
    """A slug that is free right now — `base`, then `base-2`, `base-3`, …

    Taken slugs are read in ONE query instead of probing them one at a time.

    `random_suffix` skips the counter entirely and appends random hex (used for the
    seeded demo organisation, which is never addressed by slug).
    """
    base = slugify(name)
    if random_suffix:
        return f"{base}-{secrets.token_hex(4)}"
    taken = {
        slug
        for (slug,) in db.session.query(Organization.slug).filter(
            db.or_(Organization.slug == base, Organization.slug.like(f"{base}-%"))
        )
    }
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# ── Provisioning ─────────────────────────────────────────────────────────────
def provision_organization(
    name: str, *, domain: str | None = None, demo: bool = False,
) -> Organization:
    """Create an organisation + its full default scaffolding. No users/messages."""
    # Every value below is clipped to its column width. Postgres rejects an oversized
    # string outright (a 500 on the registration screen); SQLite quietly stores it, so an
    # overflow that only bites in production never shows up in dev or in the tests.
    org_domain = (domain or D.ORGANIZATION["domain"]) if demo else domain
    org = Organization(
        name=(name or "").strip()[:128] or _("Bedrijf"),
        slug=unique_slug(name, random_suffix=demo),
        domain=(org_domain or None) and org_domain[:128],
        is_demo=demo, org_push_cats=True,
    )
    if demo:
        org.graph_app_id = D.ORGANIZATION["graph_app_id"]
        org.graph_tenant_id = D.ORGANIZATION["graph_tenant_id"]
        org.graph_consent = True
        org.graph_notifications = True
        org.gmail_pubsub = D.ORGANIZATION["gmail_pubsub"]
        org.gmail_watch = True
    db.session.add(org)
    db.session.flush()

    provision_roles(org)  # this org's own, isolated role set + permission matrix

    # Categories (+subcategories) — identical default set for every tenant.
    # resolve() turns the lazy display strings into the active locale, so a French
    # or English registrant gets their default categories in their own language.
    for i, c in enumerate(D.resolve(D.CATEGORIES)):
        cat = Category(
            organization_id=org.id, key=c["key"], name=c["name"], color=c["color"],
            ai=c["ai"], draft_enabled=c["draft_enabled"], is_default=c["is_default"],
            is_org_template=True, position=i,
        )
        db.session.add(cat)
        db.session.flush()
        for j, (sname, sai, sde) in enumerate(c["subs"]):
            db.session.add(Subcategory(category_id=cat.id, name=sname, ai=sai, draft_enabled=sde, position=j))

    # Integrations (demo keeps the connected flags; fresh tenants start disconnected).
    for i, it in enumerate(D.resolve(D.INTEGRATIONS)):
        db.session.add(Integration(
            organization_id=org.id, key=it["key"], name=it["name"], tag=it["tag"],
            color=it["color"], description=it["desc"],
            connected=it["connected"] if demo else False, position=i,
        ))

    # Provider scopes, SSO, AI, system status.
    for i, sc in enumerate(D.resolve(D.GRAPH_SCOPES)):
        db.session.add(GraphScope(organization_id=org.id, name=sc["name"], description=sc["desc"], on=sc["on"], locked=sc["locked"], position=i))

    if demo:
        db.session.add(SsoConfig(organization_id=org.id, **D.resolve(D.SSO)))
    else:
        db.session.add(SsoConfig(
            organization_id=org.id, enabled=False, method="SAML 2.0", idp="—",
            domain=domain or "", verified=False, enforce=False, mfa=False, jit=False,
            allowed_domains=[domain] if domain else [],
        ))

    db.session.add(AiConfig(organization_id=org.id, **D.resolve(D.AI)))
    for i, st in enumerate(D.resolve(D.SYSTEM_STATUS)):
        db.session.add(SystemStatus(organization_id=org.id, name=st["name"], detail=st["detail"], ok=st["ok"], position=i))

    # Every organisation starts active; the instance admin can suspend it from /owner.
    org.license = License.new_active()

    db.session.flush()
    return org


def add_owner_user(org: Organization, name: str, email: str, password: str, verified: bool = False) -> User:
    """Create the owner (Eigenaar) of a freshly provisioned org + their defaults."""
    eigenaar = org_role(org, "Eigenaar") or provision_roles(org)["Eigenaar"]
    user = User(
        email=email.lower().strip()[:255], name=name.strip()[:128], initials=initials_for(name),
        avatar_color="#6C4CE6", organization_id=org.id, role_id=eigenaar.id,
        status="Actief", last_active="nu", mailbox_count=0, onboarded=False,
        email_verified=verified,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    org.owner_user_id = user.id

    db.session.add(StyleProfile(user_id=user.id, greeting=_("Hallo"), signoff=_("Groeten"),
                                signature=_("Groeten,\n%(name)s", name=first_name(name))))
    for i, s in enumerate(D.resolve(D.SETTINGS)):
        db.session.add(Setting(user_id=user.id, key=s["key"], label=s["label"], description=s["desc"], on=s["on"], position=i))
    # actor is String(64) and a registered name may be twice that — clip like record_event does.
    db.session.add(AuditLog(organization_id=org.id, actor=first_name(name)[:64], action=_("maakte de organisatie aan"), occurred_display=_("zojuist"), kind="user", position=0))
    return user


def delete_org_deep(org: Organization) -> None:
    """Delete an organisation and ALL its data, FK-safe (works on Postgres).

    Org-level rows (categories, integrations, license, sso, …) cascade via the
    Organization relationships. Messages, tasks, feedback and users do NOT, so
    they are removed first; ORM-level deletes then trigger the remaining child
    cascades (drafts→tone variants, mail accounts→threads, settings, …).
    """
    from app.models import (
        AiUsage, DomainClaim, Feedback, KnowledgeEntry, KnowledgeSource, MailRule, Message, OrgEvent, Signature,
        Snippet,
    )

    oid = org.id
    user_ids = [uid for (uid,) in db.session.query(User.id).filter_by(organization_id=oid)]
    db.session.query(AiUsage).filter_by(organization_id=oid).delete(synchronize_session=False)
    # Same story as knowledge_sources below: non-nullable org FK, no cascade — purge
    # explicitly or the final org delete raises a FK violation on Postgres.
    db.session.query(OrgEvent).filter_by(organization_id=oid).delete(synchronize_session=False)
    db.session.query(DomainClaim).filter_by(organization_id=oid).delete(synchronize_session=False)
    db.session.query(Signature).filter_by(organization_id=oid).delete(synchronize_session=False)
    db.session.query(Snippet).filter_by(organization_id=oid).delete(synchronize_session=False)
    db.session.query(MailRule).filter_by(organization_id=oid).delete(synchronize_session=False)
    # knowledge_sources has a non-nullable org FK and no cascade — purge it explicitly,
    # otherwise the final org delete raises a FK violation (a 500 on Postgres).
    db.session.query(KnowledgeEntry).filter_by(organization_id=oid).delete(synchronize_session=False)
    db.session.query(KnowledgeSource).filter_by(organization_id=oid).delete(synchronize_session=False)

    for task in db.session.query(Task).filter_by(organization_id=oid):
        db.session.delete(task)                       # references messages + users
    if user_ids:
        for fb in db.session.query(Feedback).filter(Feedback.user_id.in_(user_ids)):
            db.session.delete(fb)                     # references messages + users
    for msg in db.session.query(Message).filter_by(organization_id=oid):
        db.session.delete(msg)                        # cascades drafts → tone variants
    # Vangnet vóór de laatste twee deletes. De opsomming hierboven is met de hand
    # bijgehouden en liep achter op de tabellen die er sindsdien bij kwamen (handtekening-
    # regels/varianten/banners, CRM, brand kits, afwezigheid, opvolgingen, profielfoto's…).
    # Zo'n gemiste tabel met een NOT NULL org-FK zonder cascade laat het definitieve wissen
    # klappen met een IntegrityError op Postgres — waardoor óók de dagelijkse
    # `purge_expired_trash()` blijft omvallen en de prullenbak nooit leegloopt.
    # Daarom sluiten we af met een sweep over de metadata: nieuwe tabellen gaan vanzelf mee.
    _purge_remaining(oid, user_ids)

    for user in db.session.query(User).filter_by(organization_id=oid):
        db.session.delete(user)                       # cascades mail accounts (+threads), settings, style profile
    db.session.delete(org)                            # cascades categories(+subcats), integrations, scopes, status, audit, sso, ai, license


def _purge_remaining(oid: int, user_ids: list[int]) -> None:
    """Verwijder alles wat nog aan deze organisatie of haar gebruikers hangt.

    Loopt de tabellen in omgekeerde afhankelijkheidsvolgorde af (kinderen eerst), zodat
    verwijderen nooit op een foreign key stuit. `organizations` en `users` zelf blijven
    staan — die worden door de aanroeper via de ORM verwijderd, zodat hun cascades draaien."""
    # Eerst de ORM-deletes hierboven laten landen, anders verwijdert deze sweep rijen die
    # de sessie nog als "te verwijderen" in beeld heeft (en waarschuwt SQLAlchemy dat zijn
    # DELETE niets meer matchte). Na afloop de identity map vergeten, zodat er niets
    # verouderds wordt hergebruikt.
    db.session.flush()
    # Tabellen die de ORM-cascade van Organization/User al correct opruimt, blijven hier
    # buiten: raw verwijderen zou die cascade juist ONDERBREKEN. Zo hangt role_permissions
    # aan roles zonder eigen organization_id — wis je `roles` rechtstreeks, dan blijven de
    # koppelrijen als wezen achter en klapt een volgende seed op hun unieke sleutel.
    ORM_CASCADED = {"organizations", "users", "roles", "role_permissions"}

    for table in reversed(db.metadata.sorted_tables):
        if table.name in ORM_CASCADED:
            continue
        if (col := table.columns.get("organization_id")) is not None:
            db.session.execute(table.delete().where(col == oid))
        elif user_ids and (ucol := table.columns.get("user_id")) is not None:
            db.session.execute(table.delete().where(ucol.in_(user_ids)))
    db.session.expire_all()


def soft_delete_org(org: Organization) -> None:
    """Mark an organisation deleted (recoverable). Its users lose app access but no
    data is removed — use delete_org_deep for a permanent purge from the trash."""
    from app.models.base import utcnow

    org.deleted_at = utcnow()
    db.session.commit()


def restore_org(org: Organization) -> None:
    """Undo a soft-delete: the organisation and its users become active again."""
    org.deleted_at = None
    db.session.commit()


def active_orgs():
    """Base query of organisations that are not soft-deleted."""
    return db.session.query(Organization).filter(Organization.deleted_at.is_(None))


def add_welcome_inbox(org: Organization, owner: User) -> None:
    """A tiny sample inbox so a brand-new tenant isn't empty on first login."""
    cats = {c.key: c for c in org.categories}
    welcome = Message(
        organization_id=org.id, user_id=owner.id, sender="Otto", sender_email="otto@example.com",
        initials="Ot", avatar_color="#6C4CE6", time_display=_("nu"), unread=True,
        category_id=cats["fyi"].id if "fyi" in cats else None,
        subject=_("Welkom bij Otto 👋"), snippet=_("Koppel je mailbox en Otto begint te sorteren…"),
        body=_("Hallo en welkom!\n\nKoppel je mailbox via Instellingen → onboarding en Otto sorteert je inbox automatisch en zet concept-antwoorden klaar. Otto verzendt nooit zelf — jij houdt de controle.\n\nVeel plezier,\nOtto"),
        lang="antwoord in Nederlands", is_seed=True,
    )
    db.session.add(welcome)

    sample = Message(
        organization_id=org.id, user_id=owner.id, sender=_("Voorbeeldklant"), sender_email="klant@voorbeeld.be",
        initials="VK", avatar_color="#2D9CDB", time_display=_("nu"), unread=True,
        category_id=cats["te_beantwoorden"].id if "te_beantwoorden" in cats else None,
        subject=_("Vraag over jullie dienst"), snippet=_("Een voorbeeldmail zodat je ziet hoe een concept eruitziet…"),
        body=_("Dag,\n\nDit is een voorbeeldmail. Otto zet hiervoor een concept-antwoord klaar dat je kan bewerken, van toon veranderen en zelf verzenden.\n\nGroeten,\nVoorbeeldklant"),
        lang="antwoord in Nederlands", is_seed=True,
    )
    db.session.add(sample)
    db.session.flush()
    first = owner.name.split()[0]
    draft = Draft(message_id=sample.id, status="concept", current_tone=DEFAULT_TONE, placed=True, lang=sample.lang,
                  current_text=_("Hallo,\n\nBedankt voor je bericht! Ik kom hier snel bij je op terug.\n\nGroeten,\n%(name)s", name=first))
    db.session.add(draft)
    db.session.flush()
    for t in TONES:
        db.session.add(DraftToneVariant(draft_id=draft.id, tone=t, text=draft.current_text))

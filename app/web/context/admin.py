"""Beheerschermen: overzicht, gebruikers, rollen, koppelingen en AI."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app
from flask_babel import gettext as _
from flask_babel import lazy_gettext as _l
from flask_login import current_user

from app.ai.metering import month_cost_eur
from app.constants import PERMISSIONS, ROLES
from app.extensions import db
from app.models import (
    AuditLog, Draft, GraphScope,
    MailAccount, Message, User,
)
from app.models.base import utcnow

from app.web.context._base import (  # noqa: F401 — gedeelde bouwstenen
    _account_messages,
    _categories,
    _category_counts,
    _messages,
    _my_shared_accounts,
    _org,
    _personal_messages,
    _visible_shared_account_ids,
    _waiting,
)

# ── Admin ────────────────────────────────────────────────────────────────────
def _org_mailbox_filter(org):
    """Filter matching every mailbox that belongs to an org: org-owned (shared) accounts
    plus the personal accounts of the org's users. Personal accounts frequently leave
    organization_id NULL (the owning user is the link), so org_id alone undercounts."""
    return db.or_(
        MailAccount.organization_id == org.id,
        MailAccount.user_id.in_(db.session.query(User.id).filter_by(organization_id=org.id)),
    )


def _nl_int(n: int) -> str:
    """Dutch thousands grouping: 3412 → '3.412'."""
    return f"{n:,}".replace(",", ".")


def _fmt_eur(amount: float) -> str:
    """Euro with a Dutch decimal comma: 0 → '€0,00', 184.5 → '€184,50'."""
    return "€" + f"{amount:.2f}".replace(".", ",")


def _fmt_seconds(seconds: float) -> str:
    """Short Dutch duration: 1.8 → '1,8 s', 92 → '1,5 min'."""
    if seconds < 60:
        return f"{seconds:.1f}".replace(".", ",") + " s"
    return f"{seconds / 60:.1f}".replace(".", ",") + " min"


def _queue_depth(org) -> int:
    """This org's waiting background tasks — strictly per-tenant (never the platform
    total), 0 in eager mode / without Redis. See app.tasks.queue_meter."""
    from app.tasks import queue_meter

    return queue_meter.pending(org.id)


def _avg_processing_24h(org_id: int) -> str | None:
    """Average mail→concept latency over the last 24h: the wall-clock time between a
    message being ingested (Message.created_at) and its concept reply being written
    (Draft.created_at). None when no drafts were produced in the window."""
    day_ago = utcnow() - timedelta(hours=24)
    rows = (
        db.session.query(Draft.created_at, Message.created_at)
        .join(Message, Draft.message_id == Message.id)
        .filter(Message.organization_id == org_id, Draft.created_at >= day_ago)
        .all()
    )
    deltas = [(d - m).total_seconds() for d, m in rows if d and m and d >= m]
    if not deltas:
        return None
    return _fmt_seconds(sum(deltas) / len(deltas))


def _live_system_status(org) -> list[dict]:
    """Real subsystem health for the Beheer overview — mirrors /owner/health, framed
    per-org. Each row is {name, detail, ok} and reflects the actual config plus a live
    DB ping, so it tracks the deployment instead of showing seeded text."""
    from sqlalchemy import text

    cfg = current_app.config
    rows = []

    # Microsoft Graph (Outlook) and Gmail are resolved PER ORG, not from the platform
    # flag: a Connect customer brings its own app, and reading the platform config showed
    # such a customer "niet geconfigureerd" for a connector that works fine for them.
    from app.connectors import gmail_configured_for, graph_configured_for

    graph_ok = graph_configured_for(org)
    graph_detail = (
        (_("Change notifications actief") if cfg.get("GRAPH_REALTIME") else _("Polling-modus (geen realtime webhooks)"))
        if graph_ok else _("Niet geconfigureerd (demo-modus)")
    )
    rows.append({"name": "Microsoft Graph API", "detail": graph_detail, "ok": graph_ok})

    # Gmail API + Pub/Sub. The detail counts mailboxes that are genuinely watched
    # (subscription_id set by users.watch) rather than every connected Gmail mailbox —
    # this row used to report "watch actief" on mailboxes nothing had ever watched.
    gmail_ok = gmail_configured_for(org)
    gmail_boxes = db.session.query(MailAccount).filter(_org_mailbox_filter(org), MailAccount.provider == "gmail")
    n_gmail = gmail_boxes.count()
    n_watched = gmail_boxes.filter(MailAccount.subscription_id.isnot(None)).count()
    if not gmail_ok:
        gmail_detail = _("Niet geconfigureerd (demo-modus)")
    elif not n_gmail:
        gmail_detail = _("Geconfigureerd — nog geen Gmail-postvak gekoppeld")
    elif cfg.get("GMAIL_REALTIME"):
        gmail_detail = _("Watch actief op %(n)s van %(total)s postvak(ken)", n=n_watched, total=n_gmail)
    else:
        missing = ", ".join(cfg.get("GMAIL_REALTIME_MISSING") or [])
        gmail_detail = _("Polling-modus op %(total)s postvak(ken) — ontbreekt: %(missing)s",
                         total=n_gmail, missing=missing)
    rows.append({"name": "Gmail API + Pub/Sub", "detail": gmail_detail, "ok": gmail_ok})

    # LLM service: the layer always works (stub fallback), so ok=True; detail names the provider.
    if cfg.get("LLM_IS_STUB"):
        llm_detail = _("Stub (offline) — geen externe LLM geconfigureerd")
    else:
        model = cfg.get("LLM_MODEL")
        llm_detail = _("Provider: %(p)s", p=cfg.get("LLM_PROVIDER")) + (f" · {model}" if model else "")
    rows.append({"name": _("LLM-service"), "detail": llm_detail, "ok": True})

    # Task queue & workers. The depth shown is this org's own waiting tasks (per-tenant).
    if cfg.get("TASK_ALWAYS_EAGER", True) or not cfg.get("REDIS_URL"):
        rows.append({"name": _("Taakwachtrij & workers"), "detail": _("Inline (eager) — geen aparte workers"), "ok": True})
    else:
        try:
            import redis

            redis.from_url(cfg["REDIS_URL"]).ping()
            rows.append({"name": _("Taakwachtrij & workers"), "detail": _("%(n)s taken in wachtrij", n=_queue_depth(org)), "ok": True})
        except Exception:  # noqa: BLE001
            rows.append({"name": _("Taakwachtrij & workers"), "detail": _("Redis onbereikbaar"), "ok": False})

    # Database: actual engine + a live SELECT 1.
    engine = (cfg.get("SQLALCHEMY_DATABASE_URI") or "").split("://", 1)[0]
    db_name = {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(engine, engine or "Database")
    try:
        db.session.execute(text("SELECT 1"))
        rows.append({"name": db_name, "detail": _("Verbonden"), "ok": True})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": db_name, "detail": str(e)[:80], "ok": False})

    return rows


def overview_context() -> dict:
    """Live platform figures for the Beheer overview — every value is computed from
    the database / runtime, scoped to the current organisation (no hardcoded demo
    figures). See helpers above for the per-metric sources."""
    org = _org()
    day_ago = utcnow() - timedelta(hours=24)

    active_users = sum(1 for u in org.users if u.status != "Geblokkeerd")
    mailboxes = db.session.query(MailAccount).filter(_org_mailbox_filter(org)).count()
    mails_24h = (
        db.session.query(Message)
        .filter(Message.organization_id == org.id, Message.created_at >= day_ago).count()
    )
    drafts_24h = (
        db.session.query(Draft).join(Message, Draft.message_id == Message.id)
        .filter(Message.organization_id == org.id, Draft.created_at >= day_ago).count()
    )
    queue = _queue_depth(org)

    stats = [
        {"value": _nl_int(active_users), "label": _("Actieve gebruikers"), "color": "#6C4CE6"},
        {"value": _nl_int(mailboxes), "label": _("Gekoppelde postvakken"), "color": "#0078D4"},
        {"value": _nl_int(mails_24h), "label": _("Mails verwerkt (24 u)"), "color": "#2D9CDB"},
        {"value": _nl_int(drafts_24h), "label": _("Concepten gegenereerd (24 u)"), "color": "#27AE60"},
        {"value": _fmt_eur(month_cost_eur(org)), "label": _("LLM-verbruik deze maand"), "color": "#9B51E0"},
        {"value": _avg_processing_24h(org.id) or "—", "label": _("Gem. verwerkingstijd"), "color": "#F2994A"},
        {"value": _nl_int(queue), "label": _("Taken in wachtrij"), "color": "#9AA4B2"},
    ]
    from app.tasks.schedule import TRASH_RETENTION_DAYS

    me = current_user
    is_owner = bool((me.role and me.role.is_owner) or org.owner_user_id == me.id)
    return {"org_stats": stats, "sys_status": _live_system_status(org),
            "org": org, "can_delete_org": is_owner, "trash_days": TRASH_RETENTION_DAYS}


def users_context() -> dict:
    users = (
        db.session.query(User)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(User.id).all()
    )
    return {"users": users, "role_options": ROLES}


def roles_context() -> dict:
    org = _org()
    from app.models import Role

    roles = db.session.query(Role).filter_by(organization_id=org.id).order_by(Role.id).all()
    role_by_name = {r.name: r for r in roles}
    rows = []
    for key, label in PERMISSIONS:
        cells = []
        for name in ROLES:
            role = role_by_name.get(name)
            cells.append({
                "role": name, "perm": key,
                "on": bool(role.allows(key)) if role else False,
                "locked": name == "Eigenaar",
            })
        rows.append({"label": label, "key": key, "cells": cells})
    return {"perm_roles": ROLES, "perm_rows": rows}


def sso_context() -> dict:
    org = _org()
    return {"sso": org.sso, "allowed_domains": (org.sso.allowed_domains or [])}


def signatures_context() -> dict:
    """Handtekeningen-builder: de handtekeningen mét hun toewijzingen, de kiezers
    (collega's + M365-groepen) en de voorbeeldwaarden voor de live preview."""
    from app.models import Signature, User
    from app.signatures import PLACEHOLDER_KEYS, RULE_FIELDS, RULE_OPS, placeholder_values

    org = _org()
    rows = (
        db.session.query(Signature)
        .filter_by(organization_id=org.id)
        .order_by(Signature.domain.is_(None).desc(), Signature.domain)
        .all()
    )
    members = (
        db.session.query(User)
        .filter_by(organization_id=org.id)
        .filter(User.deleted_at.is_(None) if hasattr(User, "deleted_at") else db.true())
        .order_by(User.name)
        .all()
    )
    user_names = {str(u.id): u.name or u.email for u in members}

    # M365-groepen live via het gekoppelde postvak van de beheerder zelf. Zonder
    # koppeling of zonder GroupMember.Read.All-consent wordt dit None → de UI legt
    # uit hoe de beheerder de groepstoewijzing aanzet.
    groups = None
    try:
        from app.providers import get_provider

        acct = (
            db.session.query(MailAccount)
            .filter_by(user_id=current_user.id, provider="outlook")
            .filter(MailAccount.mailbox_type != "shared")
            .first()
        )
        if acct is not None:
            provider = get_provider(acct)
            if provider.is_configured() and hasattr(provider, "list_directory_groups"):
                groups = provider.list_directory_groups()
    except Exception:  # noqa: BLE001 — de kiezer degradeert, het scherm blijft werken
        groups = None

    # Server-side previews voor de lijst: placeholders ingevuld met de gegevens van de
    # ingelogde beheerder, in hetzelfde iframe-document als het leespaneel gebruikt.
    from app.htmlview import sanitize_email_html, wrap_email_document
    from app.signatures import render_placeholders

    previews: dict[int, str] = {}
    for s in rows:
        if s.html:
            rendered = render_placeholders(s.html, current_user, html=True)
            previews[s.id] = wrap_email_document(sanitize_email_html(rendered) or rendered)
        else:
            import html as _html

            previews[s.id] = wrap_email_document(
                _html.escape(render_placeholders(s.body or "", current_user)).replace("\n", "<br>")
            )

    # Eigen variabelen (org-breed) + de ingevulde waarden per collega, voor de matrix.
    from app.models import SignatureAsset, SignatureBanner, SignatureVariable, SignatureVariableValue

    sig_vars = (
        db.session.query(SignatureVariable)
        .filter_by(organization_id=org.id, user_id=None)
        .order_by(SignatureVariable.id)
        .all()
    )
    # Ingebedde afbeeldingen (logo's/banners) voor de asset-lijst.
    sig_assets = (
        db.session.query(SignatureAsset)
        .filter_by(organization_id=org.id)
        .order_by(SignatureAsset.id.desc())
        .all()
    )
    # Regels (verzender/ontvanger-voorwaarden), op priority zoals de resolver ze evalueert.
    from app.models import SignatureRule

    sig_rules = (
        db.session.query(SignatureRule)
        .filter_by(organization_id=org.id)
        .order_by(SignatureRule.priority, SignatureRule.id)
        .all()
    )
    # JSON-vorm voor de "Bewerken"-knop in de front-end (voorwaarden als [veld, op, waarde]).
    sig_rules_data = [
        {"id": r.id, "name": r.name or "", "priority": r.priority, "enabled": r.enabled,
         "signature_id": r.signature_id,
         "conds": [[c.field, c.op, c.value or ""] for c in r.conditions]}
        for r in sig_rules
    ]
    sig_banners = (
        db.session.query(SignatureBanner)
        .filter_by(organization_id=org.id)
        .order_by(SignatureBanner.weight.desc(), SignatureBanner.id.desc())
        .all()
    )
    from app.models import BrandKit

    sig_brand_kits = (
        db.session.query(BrandKit)
        .filter_by(organization_id=org.id)
        .order_by(BrandKit.domain.is_(None).desc(), BrandKit.domain)
        .all()
    )
    # JSON-vorm voor de "Bewerken"-knop in de front-end (datums als datetime-local).
    def _dtl(dt):
        return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""

    sig_banners_data = [
        {"id": b.id, "name": b.name or "", "image_url": b.image_url, "link_url": b.link_url or "",
         "placement": b.placement, "weight": b.weight, "enabled": b.enabled,
         "starts_at": _dtl(b.starts_at), "ends_at": _dtl(b.ends_at),
         "scope_kind": b.scope_kind or "org", "scope_value": b.scope_value or "",
         "track": bool(getattr(b, "track", False))}
        for b in sig_banners
    ]
    from app.signature_analytics import banner_totals

    sig_banner_stats = banner_totals(org.id)
    var_values = {}
    if sig_vars:
        for v in (db.session.query(SignatureVariableValue)
                  .filter(SignatureVariableValue.variable_id.in_([x.id for x in sig_vars]))
                  .all()):
            var_values[f"{v.variable_id}-{v.user_id}"] = v.value or ""

    return {
        "signatures": rows,
        "sig_previews": previews,
        "sig_variables": sig_vars,
        "sig_var_values": var_values,
        "sig_assets": sig_assets,
        "sig_rules": sig_rules,
        "sig_rules_data": sig_rules_data,
        "rule_fields": RULE_FIELDS,
        "rule_ops": RULE_OPS,
        "sig_banners": sig_banners,
        "sig_banners_data": sig_banners_data,
        "sig_banner_stats": sig_banner_stats,
        "sig_brand_kits": sig_brand_kits,
        "sig_brand_kits_data": [
            {"id": k.id, "domain": k.domain or "", "name": k.name or "",
             "logo_url": k.logo_url or "", "accent_color": k.accent_color or "#6C4CE6",
             "socials": k.socials if isinstance(k.socials, dict) else {}}
            for k in sig_brand_kits
        ],
        "sig_members": members,
        "sig_user_names": user_names,
        "sig_groups": groups,            # None = niet beschikbaar; [] = echt geen groepen
        # Vaste M365-velden + de eigen org-brede variabelen — samen de chips in de builder.
        "placeholder_keys": (*PLACEHOLDER_KEYS, "logo", "social", *[v.key for v in sig_vars]),
        # Voorbeeldwaarden van de ingelogde beheerder, voor de live preview in de builder.
        "sig_sample_values": placeholder_values(current_user),
        # Kant-en-klare sjablonen om in de WYSIWYG-editor te laden en te bewerken
        # (ingebouwd + door de organisatie geïmporteerd).
        "sig_templates": _sig_gallery(),
        # Importeren vraagt nh3 (server-side sanitatie); zonder die dependency verbergt
        # de UI de importkaart in plaats van onveilige HTML op te slaan.
        "sig_import_available": _html_view_available(),
        "can_manage_signatures": current_user.has_perm("manageOrgCats"),
        # Banners + brand kits mogen ook door een gedelegeerde campagne-rol (marketing).
        "can_manage_campaigns": current_user.has_perm("manageOrgCats") or current_user.has_perm("manageSignatureCampaigns"),
        # Cloud-modus (Exchange transport-regel): modus + gegenereerd script + waarschuwingen.
        **_cloud_signature_context(org, rows),
        # Centraal beheer: schakelaar + wachtende gebruiker-voorstellen.
        **_central_context(org),
    }


def _central_context(org) -> dict:
    """De centraal-beheer-schakelaar + de wachtende wijzigingsvoorstellen (met leesbare
    veld→waarde-lijst per voorstel)."""
    from app.models import CloudSignatureSetting, SignatureChangeRequest, SignatureVariable, User
    from app.signature_central import CENTRAL_FIELDS

    setting = db.session.query(CloudSignatureSetting).filter_by(organization_id=org.id).first()
    field_labels = {
        "job_title": _("Functietitel"), "department": _("Afdeling"),
        "office_location": _("Locatie"), "phone": _("Telefoon"), "mobile": _("Mobiel"),
    }
    var_labels = {
        v.id: v.label for v in db.session.query(SignatureVariable)
        .filter_by(organization_id=org.id, user_id=None).all()
    }
    names = {u.id: (u.name or u.email) for u in db.session.query(User).filter_by(organization_id=org.id).all()}
    pending = []
    for r in (db.session.query(SignatureChangeRequest)
              .filter_by(organization_id=org.id, status="pending")
              .order_by(SignatureChangeRequest.id).all()):
        changes = []
        for key, val in (r.payload or {}).items():
            if key in CENTRAL_FIELDS:
                label = field_labels.get(key, key)
            elif key.startswith("var-"):
                label = var_labels.get(int(key[4:]), key) if key[4:].isdigit() else key
            else:
                label = key
            changes.append((label, val or "—"))
        pending.append({"id": r.id, "user": names.get(r.user_id, f"#{r.user_id}"), "changes": changes})
    return {
        "central_management": bool(setting and setting.central_management),
        "central_pending": pending,
    }


def _cloud_signature_context(org, signatures) -> dict:
    """Modus, de aansturende handtekening, het gegenereerde PowerShell-script en de
    onmapbare placeholders voor de cloud-modus-kaart."""
    from app.cloud_signature import (
        DEFAULT_MARKER, disclaimer_html, instructions, meeting_powershell_script,
        powershell_script, unmappable_placeholders,
    )
    from app.models import CloudSignatureSetting
    from app.signatures import default_for

    setting = db.session.query(CloudSignatureSetting).filter_by(organization_id=org.id).first()
    mode = setting.mode if setting else "client"
    marker = (setting.marker if setting else None) or DEFAULT_MARKER
    driving = None
    if setting and setting.signature_id:
        driving = next((s for s in signatures if s.id == setting.signature_id), None)
    driving = driving or default_for(org)
    script, meeting_script, unmappable = "", "", []
    if driving and driving.html:
        disclaimer = disclaimer_html(driving.html)
        script = powershell_script("Otto cloud-handtekening", disclaimer, marker)
        meeting_script = meeting_powershell_script("Otto vergader-handtekening", disclaimer, marker)
        unmappable = unmappable_placeholders(driving.html)
    return {
        "cloud_mode": mode,
        "cloud_signature_id": setting.signature_id if setting else None,
        "cloud_attested_at": setting.rule_attested_at if setting else None,
        "cloud_script": script,
        "cloud_meeting_script": meeting_script,
        "cloud_unmappable": unmappable,
        "cloud_instructions": instructions(),
        "can_manage_cloud": current_user.has_perm("manageProviders"),
    }


def _html_view_available() -> bool:
    from app.htmlview import html_view_available

    return html_view_available()


def _sig_gallery() -> list[dict]:
    from app.signature_templates import gallery

    return gallery(_org())


def autoreplies_context() -> dict:
    """Afwezigheidsassistent: de eigen gekoppelde Outlook-postvakken van de admin met hun
    huidige OOO-instelling. Delegated tokens zijn per gebruiker, dus alleen eigen postvakken."""
    from app.models import AutoReplySetting, MailAccount

    accounts = (
        db.session.query(MailAccount)
        .filter_by(user_id=current_user.id, provider="outlook")
        .order_by(MailAccount.id)
        .all()
    )
    settings = {}
    if accounts:
        settings = {
            s.mail_account_id: s
            for s in db.session.query(AutoReplySetting)
            .filter(AutoReplySetting.mail_account_id.in_([a.id for a in accounts]))
            .all()
        }
    return {
        "ar_accounts": accounts,
        "ar_settings": settings,
        "can_manage_autoreplies": current_user.has_perm("manageOrgCats"),
    }


def knowledge_context() -> dict:
    """Bedrijfskennis: de eigen blokken plus de gedeelde postvakken om ze aan te hangen,
    en de status van de SharePoint-bron zodat één scherm het hele plaatje toont."""
    from app.models import KnowledgeEntry, KnowledgeSource

    org = _org()
    entries = (
        db.session.query(KnowledgeEntry)
        .filter_by(organization_id=org.id)
        .order_by(KnowledgeEntry.account_id.isnot(None), KnowledgeEntry.id)
        .all()
    )
    shared = (
        db.session.query(MailAccount)
        .filter_by(organization_id=org.id, is_shared=True)
        .order_by(MailAccount.email)
        .all()
    )
    return {
        "entries": entries,
        "shared_mailboxes": shared,
        "sharepoint_sources": db.session.query(KnowledgeSource).filter_by(organization_id=org.id).all(),
        "can_manage_knowledge": current_user.has_perm("manageOrgCats"),
    }


def providers_context() -> dict:
    from app.connectors import graph_configured_for

    org = _org()
    scopes = (
        db.session.query(GraphScope)
        .filter_by(organization_id=org.id).order_by(GraphScope.position).all()
    )
    mailbox = next((a for a in current_user.mail_accounts
                    if a.provider == "outlook" and not a.is_shared), None)
    return {
        "org": org,
        "graph_scopes": scopes,
        # Real connection state (the screen used to hard-code "Verbonden" / consent).
        "graph_configured": graph_configured_for(org),
        "graph_admin_consented_at": org.graph_admin_consented_at,
        "mailbox_connected": mailbox is not None,
        "mailbox_status": mailbox.status if mailbox else None,
        "can_manage_providers": current_user.has_perm("manageProviders"),
    }


def orgcats_context() -> dict:
    cats = _categories()
    msgs = _messages()
    org = _org()
    return {"categories": cats, "counts": _category_counts(cats, msgs),
            "org_push": org.org_push_cats,
            "outlook_numbering": bool(org.outlook_category_numbering)}


# Bring-your-own AI provider choices (the "welk bedrijf" pick on the Connect plan).
# Module-level so the onboarding flow can reuse the exact same list as the AI screen.
BYO_PROVIDERS = [
    {"key": "anthropic", "name": "Anthropic (Claude)"},
    {"key": "openai", "name": "OpenAI (GPT)"},
    {"key": "azure", "name": "Azure OpenAI"},
    {"key": "compatible", "name": "OpenAI-compatibel (Mistral, Groq, OpenRouter, Gemini, lokaal…)"},
]

# Per-provider "how do I get an API key?" guide, shown next to the key field on the AI
# settings screen and in onboarding. Links are plain URLs (provider consoles, stable);
# steps use lazy_gettext so they follow the visitor's language. Keys match BYO_PROVIDERS.
BYO_PROVIDER_GUIDES = {
    "anthropic": {
        "links": [{"url": "https://console.anthropic.com/settings/keys", "label": "console.anthropic.com → API Keys"}],
        "steps": [
            _l("Meld je aan of maak een account op console.anthropic.com."),
            _l("Ga naar Settings → API Keys en klik “Create Key”."),
            _l("Kopieer de sleutel (begint met sk-ant-) en plak ze bij API-sleutel."),
            _l("Voeg tegoed toe onder Billing — je betaalt je verbruik rechtstreeks bij Anthropic."),
        ],
    },
    "openai": {
        "links": [{"url": "https://platform.openai.com/api-keys", "label": "platform.openai.com → API keys"}],
        "steps": [
            _l("Meld je aan op platform.openai.com."),
            _l("Open “API keys” en klik “Create new secret key”."),
            _l("Kopieer de sleutel (begint met sk-) en plak ze bij API-sleutel."),
            _l("Voeg een betaalmethode of tegoed toe onder Billing."),
        ],
    },
    "azure": {
        "links": [{"url": "https://portal.azure.com", "label": "portal.azure.com → Azure OpenAI"}],
        "steps": [
            _l("Maak in Azure een “Azure OpenAI”-resource aan (kies een EU-regio voor dataresidentie)."),
            _l("Deploy een model (bv. gpt-4o) — de deployment-naam vul je hierboven in als model."),
            _l("Kopieer onder “Keys and Endpoint” een sleutel én de endpoint-URL."),
            _l("Plak de sleutel bij API-sleutel en de endpoint bij Base-URL."),
        ],
    },
    "compatible": {
        "links": [
            {"url": "https://openrouter.ai/keys", "label": "OpenRouter → Keys"},
            {"url": "https://aistudio.google.com/apikey", "label": "Google AI Studio (Gemini)"},
            {"url": "https://console.groq.com/keys", "label": "Groq → Keys"},
        ],
        "steps": [
            _l("Kies een aanbieder met een OpenAI-compatibele API (OpenRouter, Google Gemini, Mistral, Groq of lokaal/Ollama)."),
            _l("Haal daar een API-sleutel op via een van de knoppen hieronder."),
            _l("Plak de sleutel bij API-sleutel en zet de Base-URL van die aanbieder bij Endpoint (bv. https://openrouter.ai/api/v1)."),
            _l("Vul het model-id in zoals de aanbieder het noemt."),
        ],
    },
}


def ai_context() -> dict:
    ai = _org().ai
    models = [
        {"key": "azure", "name": "Azure OpenAI (EU-regio)", "desc": _("Data in EU, GPT-klasse kwaliteit. Aanbevolen voor BE/EU."), "tag": _("Aanbevolen")},
        {"key": "openai", "name": "OpenAI", "desc": _("Snelste start, hoge kwaliteit. Let op datalocatie & DPA."), "tag": ""},
        {"key": "anthropic", "name": "Anthropic Claude", "desc": _("Sterke schrijfkwaliteit en lange context."), "tag": ""},
        {"key": "self", "name": _("Open-source (zelf-gehost)"), "desc": _("Maximale datacontrole, lagere variabele kosten, meer infra."), "tag": ""},
    ]
    for m in models:
        m["selected"] = ai.model == m["key"]
    from app.ai.models import template_context

    return {"ai": ai, "models": models, "byo_providers": BYO_PROVIDERS,
            "byo_guides": BYO_PROVIDER_GUIDES,
            "byo_has_key": bool(ai.byo_api_key_enc),
            **template_context(ai)}


def audit_context() -> dict:
    entries = (
        db.session.query(AuditLog)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(AuditLog.occurred_at.desc().nullslast(), AuditLog.position).all()
    )
    return {"audit": entries}

"""Persoonlijke schermen: taken, categorieën, integraties en instellingen."""
from __future__ import annotations

from flask import current_app
from flask_login import current_user

from app.constants import TONE_LABELS, TONES
from app.extensions import db
from app.models import Integration, MailboxMember, Task, TodoList

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

# ── Tasks ────────────────────────────────────────────────────────────────────
def tasks_context(list_id: int | None = None) -> dict:
    # Tasks are personal (To Do parity), so the page shows the current user's own.
    q = db.session.query(Task).filter_by(user_id=current_user.id)
    if list_id is not None:
        q = q.filter_by(todo_list_id=list_id)
    # Open tasks first, then completed; newest within each group.
    tasks = q.order_by(Task.done, Task.id.desc()).all()
    lists = (
        db.session.query(TodoList)
        .filter_by(user_id=current_user.id)
        .order_by(TodoList.is_default.desc(), TodoList.name)
        .all()
    )
    from datetime import date

    return {
        "tasks": tasks,
        "todo_lists": lists,
        "active_list_id": list_id,
        "open_count": sum(1 for t in tasks if not t.done),
        "today": date.today(),
    }


# ── Categories ───────────────────────────────────────────────────────────────
def categories_context() -> dict:
    cats = _categories()
    msgs = _messages()
    counts = _category_counts(cats, msgs)
    return {"categories": cats, "counts": counts}


# ── Integrations ─────────────────────────────────────────────────────────────
# Integrations that really work today, riding on the mailbox Graph token
# (Tasks.ReadWrite covers To Do AND Planner). Their tile state is *derived* from
# the live mailbox connection — never from the seeded `connected` flag, which
# would show "Binnenkort" for a working To Do sync (or a fake ✓ in reverse).
LIVE_INTEGRATION_KEYS = {"outlook", "todo"}


def integrations_context() -> dict:
    rows = (
        db.session.query(Integration)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(Integration.position)
        .all()
    )
    ms_connected = any(
        a.provider == "outlook" and a.status == "connected" and not a.is_shared
        for a in current_user.mail_accounts
    )
    integrations = []
    for it in rows:
        if it.key == "crm":
            # De CRM-placeholder-tegel is vervangen door echte CRM-kaarten hierboven
            # (app/crm/); oudere organisaties hebben de geseede rij nog.
            continue
        live = it.key in LIVE_INTEGRATION_KEYS
        integrations.append({
            "key": it.key, "name": it.name, "tag": it.tag, "color": it.color,
            "description": it.description, "live": live,
            "connected": ms_connected if live else False,
            "testable": live and it.key == "todo" and ms_connected,
        })
    # Shared (team) mailboxes the current user is a member of, with their teams.
    member_of = (
        db.session.query(MailboxMember)
        .filter_by(user_id=current_user.id)
        .all()
    )
    shared_mailboxes = []
    for m in member_of:
        acct = m.account
        if not acct or not acct.is_shared:
            continue
        shared_mailboxes.append({
            "id": acct.id,
            "address": acct.target_address or acct.email,
            "members": [mm.user.name for mm in acct.members if mm.user],
            "member_count": len(acct.members),
        })
    # A shared mailbox is reached with the user's own Microsoft grant.
    can_add_shared = any(
        a.provider == "outlook" and not a.delegated_via_account_id
        for a in current_user.mail_accounts
    )
    # Knowledge sources (SharePoint) for grounding AI drafts.

    from app.models import KnowledgeSource

    sources = (
        db.session.query(KnowledgeSource)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(KnowledgeSource.id)
        .all()
    )
    knowledge_sources = [
        {"id": s.id, "name": s.name, "site_url": s.site_url, "status": s.status} for s in sources
    ]
    org = current_user.organization
    # Eigen CRM-koppelingen — voor het loggen van klantmail in hun CRM (app/crm/).
    from app.crm import PROVIDERS
    from app.models import CrmConnection

    rows_by_provider = {
        c.provider: c
        for c in db.session.query(CrmConnection)
        .filter_by(organization_id=current_user.organization_id)
        .all()
    }
    crm_connections = []
    for key, meta in PROVIDERS.items():
        c = rows_by_provider.get(key)
        crm_connections.append({
            "provider": key,
            "name": meta["name"],
            "tag": meta["tag"],
            "color": meta["color"],
            "description": meta["description"],
            "fields": meta["fields"],
            # Niet "values" noemen: c.values zou in Jinja de dict-methode raken
            # in plaats van deze sleutel, en het formulier leeg renderen.
            "field_values": {
                "base_url": (c.base_url if c else "") or "",
                "db_name": (c.db_name if c else "") or "",
                "username": (c.username if c else "") or "",
            },
            "has_key": bool(c and c.api_key_enc),
            "enabled": bool(c and c.enabled),
            "log_incoming": bool(c.log_incoming) if c else True,
            "ready": bool(c and c.is_ready),
        })
    return {
        "integrations": integrations,
        "shared_mailboxes": shared_mailboxes,
        "can_add_shared": can_add_shared,
        "knowledge_sources": knowledge_sources,
        "knowledge_configured": bool(current_app.config.get("KNOWLEDGE_CONFIGURED")),
        "can_manage_knowledge": current_user.has_perm("manageProviders"),
        "can_manage_providers": current_user.has_perm("manageProviders"),
        "graph_admin_consented_at": org.graph_admin_consented_at if org else None,
        "crm_connections": crm_connections,
    }



# ── Settings ─────────────────────────────────────────────────────────────────
def settings_context() -> dict:

    from app.constants import SYNC_INTERVAL_OPTIONS, USER_SETTING_KEYS

    accounts = [a for a in current_user.mail_accounts if a.status == "connected"]
    default_interval = current_app.config.get("SYNC_INTERVAL_SECONDS", 300)
    # All of a user's mailboxes share one interval in the UI; show the first one's value.
    current_interval = next((a.sync_interval_seconds for a in accounts
                             if a.sync_interval_seconds), None) or default_interval
    style = current_user.style_profile
    raw = (getattr(style, "raw_profile", None) or {}) if style else {}
    return {
        # Alleen schakelaars die de pijplijn echt aanstuurt; oude prototype-rijen die
        # nog in de database staan (vip, samenvatting, …) worden niet meer getoond.
        "settings": [s for s in current_user.settings if s.key in USER_SETTING_KEYS],
        "style": style,
        # What Otto learned, and which parts the user has taken over by hand.
        "style_examples": raw.get("examples") or [],
        "style_description": raw.get("description") or "",
        "style_pinned": set(raw.get("pinned") or []),
        "tones": [{"key": t, "label": TONE_LABELS[t]} for t in TONES],
        "sync_accounts": accounts,
        "sync_last": next((a.last_sync for a in accounts if a.last_sync), None),
        "sync_interval": current_interval,
        "sync_options": [{"seconds": s, "label": label} for s, label in SYNC_INTERVAL_OPTIONS],
        # Handtekening-variabelen: org-brede (door de admin gedefinieerd) + eigen
        # persoonlijke, elk met de eigen ingevulde waarde — voor de Mijn-profiel-kaart.
        "profile_vars": _profile_vars(),
        # Centraal beheer: staat dit aan, dan gaan wijzigingen aan de gedeelde velden via
        # een goedkeuringsflow. sig_pending = het lopende voorstel van deze gebruiker (of None).
        "sig_central": _sig_central(),
        "sig_pending": _sig_pending(),
    }


def _sig_central() -> bool:
    from app.signature_central import is_central

    return is_central(current_user.organization)


def _sig_pending():
    from app.models import SignatureChangeRequest

    return (db.session.query(SignatureChangeRequest)
            .filter_by(user_id=current_user.id, status="pending").first())


def _profile_vars() -> list[dict]:
    from app.signatures import variables_for

    return [{"id": var.id, "key": var.key, "label": var.label,
             "personal": var.is_personal, "value": value}
            for var, value in variables_for(current_user)]




# ── Inbox-regels ─────────────────────────────────────────────────────────────
def rules_context(account_id: int | None = None) -> dict:
    """Regels van één mailbox, plus de mailboxen waaruit gekozen kan worden."""
    from app import mailrules as svc
    from app.models import MailAccount

    # Op eigenaarschap/lidmaatschap scopen en niet op organization_id: dat veld is op
    # oudere mailboxen leeg, en die zouden dan onzichtbaar blijven op dit scherm.
    accounts = [
        a for a in db.session.query(MailAccount)
        .filter_by(status="connected")
        .order_by(MailAccount.id)
        .all()
        if (a.user_id == current_user.id) or (a.is_shared and current_user.id in a.member_user_ids)
    ]
    selected = next((a for a in accounts if a.id == account_id), None) or (accounts[0] if accounts else None)

    rules, folders, supported = [], [], False
    if selected is not None:
        from app.providers import get_provider

        rules = svc.rules_for(selected)
        try:
            provider = get_provider(selected)
            # Of dit sóórt mailbox regels kent — niet of de koppeling nu compleet is.
            # Zo kan je regels alvast opstellen; het pushen wacht dan tot de koppeling
            # rond is, en per regel is zichtbaar dat ze nog niet in Outlook staat.
            supported = bool(getattr(provider, "supports_rules", False))
            if supported and provider.is_configured():
                folders = [f["name"] for f in (provider.list_folders() or []) if f.get("name")]
        except Exception:  # noqa: BLE001 — zonder mappenlijst blijft het scherm bruikbaar
            supported = getattr(selected, "provider", "") == "outlook"

    return {
        "accounts": accounts,
        "selected_account": selected,
        "rules": [
            {"rule": r, "conditions": svc.describe(r)[0], "actions": svc.describe(r)[1]}
            for r in rules
        ],
        "folders": folders,
        "rules_supported": supported,
        "condition_types": [(k, lbl, kind) for k, (lbl, kind) in svc.CONDITIONS.items()],
        "action_types": [(k, lbl, kind) for k, (lbl, kind) in svc.ACTIONS.items()],
        "importance_levels": svc.IMPORTANCE,
        "destructive": sorted(svc.DESTRUCTIVE),
        # Alles wat de regelbouwer nodig heeft, in één blok. Hier samengesteld in plaats
        # van in de template: JSON met de hand in Jinja bouwen gaat één keer per jaar mis
        # op een apostrof in een mapnaam, en dan staat het hele scherm stil.
        "rule_catalog": {
            "conditions": [{"key": k, "label": str(lbl), "kind": kind}
                           for k, (lbl, kind) in svc.CONDITIONS.items()],
            "actions": [{"key": k, "label": str(lbl), "kind": kind,
                         "destructive": k in svc.DESTRUCTIVE}
                        for k, (lbl, kind) in svc.ACTIONS.items()],
            "importance": list(svc.IMPORTANCE),
            "folders": folders,
        },
    }

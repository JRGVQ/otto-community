"""Teams-omgeving: gedeelde mailboxen, hun leden en instellingen."""
from __future__ import annotations

from flask_babel import gettext as _
from flask_login import current_user

from app.constants import TONE_LABELS, TONES
from app.extensions import db
from app.models import MailAccount, MailboxMember, Message, User

from app.snippets import for_account as snippets_for
from app.web.context.inbox import _build_inbox_view
from app.web.context._base import (  # noqa: F401 — gedeelde bouwstenen
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

# ── Teams (shared mailboxes) ─────────────────────────────────────────────────
def teams_context() -> dict:
    """The shared mailboxes the current user is a member of — one card each."""
    from app.constants import MAILBOX_ROLE_LABELS

    from app.models import Draft

    member_of = (
        db.session.query(MailboxMember).filter_by(user_id=current_user.id).all()
    )
    mailboxes = []
    for m in member_of:
        acct = m.account
        if not acct or not acct.is_shared:
            continue
        s = acct.settings
        # Lightweight activity stats so a card actually says something at a glance.
        draft_count = (
            db.session.query(Draft).join(Message, Draft.message_id == Message.id)
            .filter(Message.account_id == acct.id, Draft.status == "concept").count()
        )
        unread_count = (
            db.session.query(Message)
            .filter(Message.account_id == acct.id, Message.unread.is_(True), Message.archived.is_(False))
            .count()
        )
        avatars = [
            {"initials": mm.user.initials or (mm.user.name[:1].upper() if mm.user.name else "?"),
             "color": mm.user.avatar_color or "#6C4CE6"}
            for mm in acct.members if mm.user
        ]
        mailboxes.append({
            "id": acct.id,
            "name": (s.name if s else (acct.target_address or acct.email)),
            "address": acct.target_address or acct.email,
            "color": (s.color if (s and s.color) else "#5B5FC7"),
            "description": (s.description if s else None),
            "members": [mm.user.name for mm in acct.members if mm.user],
            "member_count": len(acct.members),
            "avatars": avatars,
            "my_role": MAILBOX_ROLE_LABELS.get(m.role or "agent", _("Behandelaar")),
            "auto_draft": (bool(s.auto_draft) if s else True),
            "draft_count": draft_count,
            "unread_count": unread_count,
        })
    mailboxes.sort(key=lambda x: x["name"].lower())
    can_add_shared = any(
        a.provider == "outlook" and not a.delegated_via_account_id
        for a in current_user.mail_accounts
    )
    return {"mailboxes": mailboxes, "can_add_shared": can_add_shared}


def _resolve_team_mailbox(account_id: int):
    """Return the shared mailbox if the current user may work it, else None.

    Enforces: exists, is shared, same org and the user is a member. Ensures a MailboxSettings row exists (created on first access;
    column defaults apply on insert)."""
    acct = db.session.get(MailAccount, account_id)
    if not acct or not acct.is_shared or acct.organization_id != current_user.organization_id:
        return None
    if current_user.id not in acct.member_user_ids:
        return None
    if acct.settings is None:
        from app.models import MailboxSettings

        db.session.add(MailboxSettings(
            mail_account_id=acct.id, display_name=acct.target_address or acct.email))
        try:
            db.session.commit()
        except Exception:  # noqa: BLE001 — a concurrent first-access already created it
            db.session.rollback()
    return acct


def _team_header(acct) -> dict:
    s = acct.settings
    # Sibling shared mailboxes the user also works, for the in-inbox switcher.
    siblings = []
    for mm in db.session.query(MailboxMember).filter_by(user_id=current_user.id).all():
        sib = mm.account
        if sib and sib.is_shared:
            ss = sib.settings
            siblings.append({
                "id": sib.id,
                "name": (ss.name if ss else (sib.target_address or sib.email)),
            })
    siblings.sort(key=lambda x: x["name"].lower())
    return {
        "id": acct.id,
        "name": (s.name if s else (acct.target_address or acct.email)),
        "address": acct.target_address or acct.email,
        "color": (s.color if (s and s.color) else "#5B5FC7"),
        "member_count": len(acct.members),
        "siblings": siblings,
    }


def team_inbox_context(
    acct,
    filter_key: str,
    selected_id: int | None,
    search: str = "",
    unread_only: bool = False,
    sort: str = "new",
) -> dict:
    """One shared mailbox's inbox — the same screen as the personal inbox, scoped to it."""
    data = _build_inbox_view(
        _account_messages(acct.id), _categories(), filter_key, selected_id,
        link_endpoint="web.team_inbox", link_args={"account_id": acct.id},
        search=search, unread_only=unread_only, sort=sort,
    )
    data["show_viewswitch"] = False
    data["team"] = _team_header(acct)
    # De digest-modus is een persoonlijke voorkeur; een gedeeld postvak is van het team.
    # Expliciet op None, zodat het gedeelde scherm niet op een ongedefinieerde variabele
    # in de template leunt.
    data["digest"] = None
    # Members this mail can be assigned to (collaboration, fase 4).
    data["assignees"] = [
        {"id": mm.user_id, "name": mm.user.name} for mm in acct.members if mm.user
    ]
    return data


def team_analytics_context(acct) -> dict:
    """Cijfers over één gedeelde mailbox: volume, doorlooptijd, SLA en werkverdeling.

    Bewust op de mailbox en niet op de persoon: een teamleider wil weten hoe het postvak
    ervoor staat, niet hoe snel één collega typt. De reactietijd is de mediaan, niet het
    gemiddelde — één mail die drie weken bleef liggen zou een gemiddelde onbruikbaar maken.
    """
    from collections import Counter
    from datetime import timedelta

    from app.constants import DRAFT_CONCEPT, DRAFT_DISCARDED, DRAFT_SENT
    from app.models.base import utcnow

    msgs = _account_messages(acct.id)
    settings = acct.settings
    now = utcnow()
    week_ago = now - timedelta(days=7)
    fortnight_ago = now - timedelta(days=14)

    # ── Volume ────────────────────────────────────────────────────────────
    this_week = sum(1 for m in msgs if m.received_at and m.received_at >= week_ago)
    last_week = sum(
        1 for m in msgs if m.received_at and fortnight_ago <= m.received_at < week_ago
    )
    trend = None if not last_week else round((this_week - last_week) * 100 / last_week)

    # ── Reactietijd + SLA ─────────────────────────────────────────────────
    # Alleen mails waar we écht op antwoordden en waar beide tijdstempels bekend zijn.
    replied = [
        (m.replied_at - m.received_at).total_seconds() / 60
        for m in msgs
        if m.replied_at and m.received_at and m.replied_at >= m.received_at
    ]
    median_minutes = _median(replied)
    sla = settings.sla_minutes if settings else None
    within_sla = sum(1 for r in replied if r <= sla) if (sla and replied) else 0
    sla_pct = round(within_sla * 100 / len(replied)) if (sla and replied) else None

    # ── Openstaand ────────────────────────────────────────────────────────
    states = Counter((m.state or "open") for m in msgs)
    state_labels = {
        "open": _("open"), "in_progress": _("in behandeling"),
        "awaiting_reply": _("wacht op antwoord"), "done": _("afgehandeld"),
    }
    by_state = [
        {"key": k, "label": label, "count": states.get(k, 0)}
        for k, label in state_labels.items()
    ]

    # ── Concepten ─────────────────────────────────────────────────────────
    drafts = Counter()
    for m in msgs:
        if m.draft and m.draft.status:
            drafts[m.draft.status] += 1

    # ── Werkverdeling over de leden ───────────────────────────────────────
    by_member = []
    for mm in acct.members:
        if not mm.user:
            continue
        mine = [m for m in msgs if m.assignee_user_id == mm.user_id]
        by_member.append({
            "name": mm.user.name,
            "initials": mm.user.initials or "",
            "avatar_color": mm.user.avatar_color or "#6C4CE6",
            "assigned": len(mine),
            "replied": sum(1 for m in mine if m.replied_at),
            "open": sum(1 for m in mine if (m.state or "open") != "done"),
        })
    by_member.sort(key=lambda x: x["assigned"], reverse=True)
    unassigned = sum(1 for m in msgs if m.assignee_user_id is None)

    # ── Per categorie ─────────────────────────────────────────────────────
    cats = _categories()
    counts = _category_counts(cats, msgs)
    denom = len(msgs) or 1
    by_cat = sorted(
        ({"name": c.name, "color": c.color, "count": counts.get(c.id, 0),
          "pct": round(counts.get(c.id, 0) * 100 / denom)} for c in cats),
        key=lambda x: x["count"], reverse=True,
    )

    return {
        "team": _team_header(acct),
        "account": acct,
        "total": len(msgs),
        "unread": sum(1 for m in msgs if m.unread),
        "this_week": this_week,
        "last_week": last_week,
        "trend": trend,
        "replied_count": len(replied),
        "median_response": _human_minutes(median_minutes),
        "sla_minutes": sla,
        "sla_pct": sla_pct,
        "by_state": by_state,
        "draft_counts": {
            "concept": drafts.get(DRAFT_CONCEPT, 0),
            "verzonden": drafts.get(DRAFT_SENT, 0),
            "verworpen": drafts.get(DRAFT_DISCARDED, 0),
        },
        "by_member": by_member,
        "unassigned": unassigned,
        "by_cat": by_cat,
        "can_manage": _can_manage_mailbox(acct),
    }


def _can_manage_mailbox(acct) -> bool:
    """Whether the current user may configure this mailbox: its owner-member, or an
    org owner/admin."""
    me_member = next((mm for mm in acct.members if mm.user_id == current_user.id), None)
    return bool(
        (me_member and me_member.role == "owner")
        or (current_user.role and current_user.role.is_owner)
        or (current_user.organization and current_user.organization.owner_user_id == current_user.id)
    )


def team_settings_context(acct) -> dict:
    """Settings page for one shared mailbox."""
    from app.constants import COLOR_CHOICES, MAILBOX_ROLE_LABELS, MAILBOX_ROLES

    members = []
    for mm in acct.members:
        if not mm.user:
            continue
        members.append({
            "user_id": mm.user_id,
            "name": mm.user.name,
            "email": mm.user.email,
            "initials": mm.user.initials or "",
            "avatar_color": mm.user.avatar_color or "#6C4CE6",
            "role": mm.role or "agent",
            "role_label": MAILBOX_ROLE_LABELS.get(mm.role or "agent", _("Behandelaar")),
            "can_draft": bool(mm.can_draft),
            "can_send": bool(mm.can_send),
            "is_me": mm.user_id == current_user.id,
        })
    members.sort(key=lambda x: (x["role"] != "owner", x["name"].lower()))

    # Org users who could still be added to this mailbox (not already members).
    member_ids = set(acct.member_user_ids)
    q = db.session.query(User).filter(
        User.organization_id == current_user.organization_id,
        User.status != "Geblokkeerd",
    )
    if member_ids:
        q = q.filter(~User.id.in_(member_ids))
    addable_users = [
        {"id": u.id, "name": u.name, "email": u.email} for u in q.order_by(User.name).all()
    ]

    # Effective per-mailbox category config (fase 3) — inherits org defaults.
    from app.models.mailbox_category import effective_config, effective_foldering

    cats = _categories()
    mailbox_categories = []
    for c in cats:
        enabled, draft_on, ai_extra = effective_config(acct, c)
        keep_in_inbox, target_folder = effective_foldering(acct, c)
        mailbox_categories.append({
            "id": c.id, "name": c.name, "color": c.color,
            "enabled": enabled, "draft_enabled": draft_on, "ai_override": ai_extra or "",
            "keep_in_inbox": keep_in_inbox, "target_folder": target_folder or "",
        })

    return {
        "account": acct,
        "settings": acct.settings,
        "team": _team_header(acct),
        "members": members,
        "addable_users": addable_users,
        "member_roles": [{"key": k, "label": MAILBOX_ROLE_LABELS[k]} for k in MAILBOX_ROLES],
        "categories": cats,
        "mailbox_categories": mailbox_categories,
        "tones": [{"key": t, "label": TONE_LABELS[t]} for t in TONES],
        "color_choices": COLOR_CHOICES,
        "can_manage": _can_manage_mailbox(acct),
        "snippets": snippets_for(acct.organization, acct),
    }



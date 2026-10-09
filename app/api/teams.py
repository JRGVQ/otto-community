"""Team (shared mailbox) member management — mailbox managers only.

Adding/removing/updating the people assigned to a shared mailbox. Access is gated
by context._resolve_team_mailbox (member + plan) AND _can_manage_mailbox (owner of
the mailbox, or an org owner/admin). Members must be existing org users — they work
the mailbox inside Otto; the mailbox itself stays synced via one connector's grant.
"""
from __future__ import annotations

from flask import request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import ok
from app.constants import MAILBOX_ROLES
from app.extensions import db
from app.models import Category, MailAccount, MailboxCategory, MailboxMember, User
from app.web import context as ctx


def _payload():
    return request.get_json(silent=True) or request.form


def _truthy(v) -> bool:
    return v in (True, "true", "on", "1", 1)


def _manageable(account_id: int):
    """The shared mailbox if the caller may manage it, else None."""
    acct = ctx._resolve_team_mailbox(account_id)
    if acct is None or not ctx._can_manage_mailbox(acct):
        return None
    return acct


@api_bp.route("/teams/<int:account_id>/members", methods=["POST"])
@login_required
def add_member(account_id: int):
    acct = _manageable(account_id)
    if acct is None:
        return ok(error="Je hebt geen beheerrechten voor dit postvak."), 403
    data = _payload()
    try:
        uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        return ok(error="Kies een gebruiker."), 400
    user = db.session.get(User, uid)
    if not user or user.organization_id != current_user.organization_id:
        return ok(error="Onbekende gebruiker."), 404
    if uid in acct.member_user_ids:
        return ok(error="Deze gebruiker is al lid van dit postvak."), 400
    role = data.get("role") if data.get("role") in MAILBOX_ROLES else "agent"
    db.session.add(MailboxMember(
        mail_account_id=acct.id, user_id=uid, role=role,
        can_draft=_truthy(data.get("can_draft")) if "can_draft" in data else True,
        can_send=_truthy(data.get("can_send")) if "can_send" in data else True,
    ))
    db.session.commit()
    return ok(toast=f"{user.name} toegevoegd aan dit postvak.", reload=True)


@api_bp.route("/teams/<int:account_id>/members/<int:user_id>", methods=["POST"])
@login_required
def update_member(account_id: int, user_id: int):
    acct = _manageable(account_id)
    if acct is None:
        return ok(error="Je hebt geen beheerrechten voor dit postvak."), 403
    m = next((mm for mm in acct.members if mm.user_id == user_id), None)
    if not m:
        return ok(error="Geen lid van dit postvak."), 404
    data = _payload()
    if data.get("role") in MAILBOX_ROLES:
        m.role = data.get("role")
    if "can_draft" in data:
        m.can_draft = _truthy(data.get("can_draft"))
    if "can_send" in data:
        m.can_send = _truthy(data.get("can_send"))
    db.session.commit()
    return ok(toast="Lid bijgewerkt.")


@api_bp.route("/teams/<int:account_id>/members/<int:user_id>", methods=["DELETE", "POST"])
@api_bp.route("/teams/<int:account_id>/members/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_member(account_id: int, user_id: int):
    acct = _manageable(account_id)
    if acct is None:
        return ok(error="Je hebt geen beheerrechten voor dit postvak."), 403
    members = list(acct.members)
    target = next((mm for mm in members if mm.user_id == user_id), None)
    if not target:
        return ok(error="Geen lid van dit postvak."), 404
    if len(members) <= 1:
        return ok(error="Het laatste lid kan niet verwijderd worden — koppel het postvak los via Integraties."), 400

    # If the leaver provides the delegated sync grant, hand it to another member,
    # mirroring api/shared_mailboxes.leave_shared so the mailbox keeps syncing.
    if acct.delegated_via_account_id:
        via = db.session.get(MailAccount, acct.delegated_via_account_id)
        if via and via.user_id == user_id:
            remaining = [uid for uid in acct.member_user_ids if uid != user_id]
            new_via = (
                db.session.query(MailAccount)
                .filter(
                    MailAccount.user_id.in_(remaining),
                    MailAccount.provider == "outlook",
                    MailAccount.delegated_via_account_id.is_(None),
                )
                .first()
            )
            if not new_via:
                return ok(error="Dit lid levert de verbinding; geen ander lid kan het postvak bereiken."), 400
            acct.delegated_via_account_id = new_via.id
            acct.user_id = new_via.user_id

    db.session.delete(target)
    db.session.commit()
    return ok(toast="Lid verwijderd.", reload=True)


@api_bp.route("/teams/<int:account_id>/categories", methods=["POST"])
@login_required
def save_categories(account_id: int):
    """Upsert this mailbox's per-category config (enabled / draft / AI override)."""
    acct = _manageable(account_id)
    if acct is None:
        return ok(error="Je hebt geen beheerrechten voor dit postvak."), 403
    items = _payload().get("categories") or []
    valid = {
        c.id for c in db.session.query(Category)
        .filter_by(organization_id=current_user.organization_id).all()
    }
    existing = {mc.category_id: mc for mc in acct.mailbox_categories}
    for it in items:
        try:
            cid = int(it.get("category_id"))
        except (TypeError, ValueError):
            continue
        if cid not in valid:
            continue
        mc = existing.get(cid)
        if mc is None:
            mc = MailboxCategory(mail_account_id=acct.id, category_id=cid)
            db.session.add(mc)
        mc.enabled = _truthy(it.get("enabled"))
        mc.draft_enabled = _truthy(it.get("draft_enabled"))
        mc.ai_override = (it.get("ai_override") or "").strip() or None
        keep = _truthy(it.get("keep_in_inbox"))
        mc.keep_in_inbox = keep
        mc.target_folder = None if keep else ((it.get("target_folder") or "").strip() or None)
    db.session.commit()
    return ok(toast="Categorie-instellingen opgeslagen.", reload=True)

"""Shared (team) mailbox endpoints.

A user connects an Exchange shared mailbox they have delegate access to. Otto then
categorises + drafts for it exactly like a personal mailbox. Access is proven by a
live Graph probe (there is no Graph API to *list* a user's shared mailboxes), so any
authenticated user may self-add the ones they can actually reach. Only the assigned
members of a shared mailbox see its mail (visibility is scoped in app/web/context.py
and app/api/_helpers.py).

The shared mailbox is a single MailAccount row per (org, address): the sync engine the
pipeline already understands. Extra members who add the same address simply *join* it —
no second sync row, no duplicated mail.
"""
from __future__ import annotations

import urllib.error

from flask import request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import ok
from app.extensions import db
from app.models import AuditLog, MailAccount, MailboxMember, MailboxSettings
from app.providers.outlook import OutlookProvider
from app.tasks.enqueue import enqueue
from app.tasks.sync import initial_import


def _personal_outlook(user) -> MailAccount | None:
    """The user's own (non-shared) Outlook account — its token reaches shared mailboxes.

    Personal accounts never carry delegated_via_account_id; shared ones always do, so
    this is robust regardless of the (NULL-able) mailbox_type column."""
    return next(
        (a for a in user.mail_accounts if a.provider == "outlook" and not a.delegated_via_account_id),
        None,
    )


def _personal_outlook_for(user_id: int) -> MailAccount | None:
    return (
        db.session.query(MailAccount)
        .filter_by(user_id=user_id, provider="outlook")
        .filter(MailAccount.delegated_via_account_id.is_(None))
        .first()
    )


def _audit(org_id: int, action: str) -> None:
    db.session.add(AuditLog(
        organization_id=org_id, actor=current_user.name, action=action,
        kind="provider", occurred_display="zojuist",
    ))
    db.session.commit()


def _probe_access(personal: MailAccount, address: str) -> tuple[bool, str]:
    """Confirm the signed-in user can actually reach `address` (delegate/full access).

    Builds a transient shared-style account that borrows `personal`'s token and hits
    the shared mailbox's inbox. 403/404 → no access; 401 → token stale (retry once)."""
    probe = MailAccount(
        user_id=personal.user_id, organization_id=personal.organization_id, provider="outlook",
        email=address, mailbox_type="shared", target_address=address,
        delegated_via_account_id=personal.id,
    )
    provider = OutlookProvider(probe)
    try:
        provider._get(f"{provider._root}/mailFolders/inbox?$select=id")
        return True, ""
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return False, "Je hebt geen toegang tot dit postvak. Vraag de beheerder om je delegate-toegang te geven."
        if exc.code == 401:
            try:
                provider.refresh_token()
                provider._get(f"{provider._root}/mailFolders/inbox?$select=id")
                return True, ""
            except Exception:  # noqa: BLE001
                return False, "Koppel je eigen Microsoft 365-postvak opnieuw en probeer dan nog eens."
        return False, f"Kon de toegang niet controleren (foutcode {exc.code})."
    except Exception:  # noqa: BLE001 — not-configured, network, etc.
        return False, "Kon de toegang tot dit postvak niet controleren."


@api_bp.route("/shared-mailboxes/verify", methods=["POST"])
@login_required
def verify_shared():
    data = request.get_json(silent=True) or request.form
    address = (data.get("address") or "").strip().lower()
    if not address or "@" not in address:
        return ok(accessible=False, reason="Geef een geldig e-mailadres op.")
    personal = _personal_outlook(current_user)
    if not personal:
        return ok(accessible=False, reason="Koppel eerst je eigen Microsoft 365-postvak.")
    accessible, reason = _probe_access(personal, address)
    return ok(accessible=accessible, reason=reason)


@api_bp.route("/shared-mailboxes", methods=["POST"])
@login_required
def add_shared():
    """Connect (or join) a shared mailbox the caller has delegate access to."""
    data = request.get_json(silent=True) or request.form
    address = (data.get("address") or "").strip().lower()
    if not address or "@" not in address:
        return ok(error="Geef een geldig e-mailadres op."), 400
    personal = _personal_outlook(current_user)
    if not personal:
        return ok(error="Koppel eerst je eigen Microsoft 365-postvak."), 400
    accessible, reason = _probe_access(personal, address)
    if not accessible:
        return ok(error=reason or "Geen toegang tot dit postvak."), 403

    org_id = current_user.organization_id
    existing = (
        db.session.query(MailAccount)
        .filter_by(organization_id=org_id, mailbox_type="shared", target_address=address)
        .first()
    )
    if existing:
        if current_user.id not in existing.member_user_ids:
            db.session.add(MailboxMember(mail_account_id=existing.id, user_id=current_user.id))
            db.session.commit()
            _audit(org_id, f"sloot zich aan bij gedeeld postvak {address}")
        return ok(toast=f"Je werkt nu mee aan {address}.", account_id=existing.id)

    account = MailAccount(
        user_id=current_user.id, organization_id=org_id, provider="outlook", email=address,
        status="connected", mailbox_type="shared", target_address=address,
        delegated_via_account_id=personal.id, last_sync="zojuist",
    )
    db.session.add(account)
    db.session.flush()
    # The member who connects the mailbox owns it (may configure it); later joiners
    # default to "agent". Give it a settings row up front so the Teams screen is clean.
    db.session.add(MailboxMember(mail_account_id=account.id, user_id=current_user.id, role="owner"))
    db.session.add(MailboxSettings(mail_account_id=account.id, display_name=address))
    db.session.commit()
    enqueue(initial_import, account.id)
    _audit(org_id, f"koppelde gedeeld postvak {address}")
    return ok(toast=f"Gedeeld postvak {address} gekoppeld.", account_id=account.id)


@api_bp.route("/shared-mailboxes/<int:account_id>/leave", methods=["POST", "DELETE"])
@login_required
def leave_shared(account_id: int):
    """Leave a shared mailbox. Re-points the sync grant to another member, or removes
    the mailbox (and its cached mail) when the last member leaves."""
    account = db.session.get(MailAccount, account_id)
    if not account or not account.is_shared or account.organization_id != current_user.organization_id:
        return ok(error="onbekend"), 404

    member_ids = account.member_user_ids
    if current_user.id not in member_ids:
        return ok(error="Je bent geen lid van dit postvak."), 403

    for m in list(account.members):
        if m.user_id == current_user.id:
            db.session.delete(m)
    remaining_ids = [uid for uid in member_ids if uid != current_user.id]

    if not remaining_ids:
        # Last member out: GDPR cascade clears tokens + cached messages/drafts.
        addr = account.target_address
        db.session.delete(account)
        db.session.commit()
        _audit(current_user.organization_id, f"verwijderde gedeeld postvak {addr}")
        return ok(toast="Gedeeld postvak losgekoppeld — gegevens verwijderd.")

    # If the leaver provided the delegated grant, hand sync to another member.
    my_personal = _personal_outlook(current_user)
    if my_personal and account.delegated_via_account_id == my_personal.id:
        new_personal = _personal_outlook_for(remaining_ids[0])
        if new_personal:
            account.delegated_via_account_id = new_personal.id
            account.user_id = remaining_ids[0]
    db.session.commit()
    _audit(current_user.organization_id, f"verliet gedeeld postvak {account.target_address}")
    return ok(toast="Je werkt niet langer mee aan dit postvak.")

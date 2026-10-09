"""Follow-up endpoints — edit / send / discard the reminder concepts.

Sending is the ONLY place a follow-up leaves Otto; it needs the `sendMail` permission
and an explicit user click. The sweep (app/tasks/followup.py) only ever prepares concepts.
"""
from __future__ import annotations

from flask import abort, current_app, flash, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import fail, ok
from app.constants import DRAFT_CONCEPT, DRAFT_DISCARDED, DRAFT_SENT
from app.extensions import db
from app.models import FollowUp
from app.security.rbac import require_perm


def _body():
    return request.get_json(silent=True) or request.form


def _get_followup(followup_id: int) -> FollowUp:
    fu = db.session.get(FollowUp, followup_id)
    if not fu or fu.organization_id != current_user.organization_id:
        abort(404)
    acct = fu.account
    # Personal follow-ups are private to their owner; shared ones are visible to members.
    if acct is None:
        visible = fu.user_id == current_user.id
    elif acct.is_shared:
        visible = current_user.id in acct.member_user_ids
    else:
        visible = acct.user_id == current_user.id
    if not visible:
        abort(404)
    return fu


@api_bp.route("/follow-ups/<int:followup_id>", methods=["PUT"])
@login_required
@require_perm("editDrafts")
def edit_followup(followup_id: int):
    fu = _get_followup(followup_id)
    if fu.status == DRAFT_CONCEPT:
        fu.current_text = _body().get("text", "") or ""
        db.session.commit()
    return ok()


@api_bp.route("/follow-ups/<int:followup_id>/discard", methods=["POST"])
@login_required
@require_perm("editDrafts")
def discard_followup(followup_id: int):
    fu = _get_followup(followup_id)
    fu.status = DRAFT_DISCARDED
    # Clean up the provider-side draft so it doesn't linger in Concepten.
    if fu.provider_draft_id and fu.account:
        try:
            from app.providers import get_provider

            get_provider(fu.account).delete_draft(fu.provider_draft_id)
        except Exception:  # noqa: BLE001
            current_app.logger.info("[followup] provider draft delete skipped for %s", fu.id)
    db.session.commit()
    flash(_("Opvolging verworpen"), "toast")
    return ok(reload=True)


@api_bp.route("/follow-ups/<int:followup_id>/send", methods=["POST"])
@login_required
def send_followup(followup_id: int):
    # Explicit, user-initiated send only — gated by the sendMail permission.
    if not current_user.has_perm("sendMail"):
        abort(403)
    fu = _get_followup(followup_id)
    if fu.status != DRAFT_CONCEPT:
        return ok(error=_("al afgehandeld")), 400

    if fu.account and fu.provider_message_id:
        from app.providers import get_provider

        provider = get_provider(fu.account)
        if provider.is_configured() and hasattr(provider, "send_user_draft"):
            try:
                # Rebuild the provider draft from the (possibly edited) text so what goes
                # out always matches what the user sees, then send it.
                if fu.provider_draft_id:
                    try:
                        provider.delete_draft(fu.provider_draft_id)
                    except Exception:  # noqa: BLE001
                        pass
                # Zelfde handtekeningpad als de sweep (app/tasks/followup.py): geldt er
                # een rijke org-handtekening, dan zit die niet in de tekst zelf en moet
                # hij hier mee — anders vertrekt de opvolging zonder handtekening.
                sig_html = None
                try:
                    from app.signatures import resolve_rich

                    mb = fu.account.settings if fu.account.is_shared else None
                    sig_html = resolve_rich(fu.account.user, account=fu.account,
                                            mailbox_settings=mb).html
                except Exception:  # noqa: BLE001 — handtekening mag verzenden nooit blokkeren
                    pass
                extra = {"signature_html": sig_html} if sig_html else {}
                draft_id = provider.create_followup_draft(fu.provider_message_id,
                                                          fu.current_text or "", **extra)
                if not draft_id:
                    return fail(_("Verzenden mislukt — probeer het opnieuw."), 502)
                fu.provider_draft_id = draft_id
                provider.send_user_draft(draft_id)
            except Exception as exc:  # noqa: BLE001
                current_app.logger.warning("follow-up send failed for %s: %s", fu.id, exc)
                return fail(_("Verzenden mislukt — probeer het opnieuw."), 502)

    fu.status = DRAFT_SENT
    db.session.commit()
    flash(_("Opvolging verzonden — door jou"), "toast")
    return ok(reload=True)

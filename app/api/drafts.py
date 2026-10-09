"""Draft endpoints.

The send endpoint is the ONLY place a reply leaves Otto, it requires the `sendMail`
permission and an explicit user click, and it is never reachable from the pipeline.
"""
from __future__ import annotations

from flask import abort, current_app, flash, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import fail, get_message, ok
from app.constants import DEFAULT_TONE, DRAFT_CONCEPT, DRAFT_DISCARDED, DRAFT_SENT, TONES
from app.extensions import db
from app.models import Draft, DraftToneVariant, Feedback
from app.security.rbac import require_perm


def _body():
    return request.get_json(silent=True) or request.form


@api_bp.route("/drafts/<int:message_id>/tone", methods=["POST"])
@login_required
@require_perm("editDrafts")
def set_tone(message_id: int):
    msg = get_message(message_id)
    draft = msg.draft
    tone = _body().get("tone", DEFAULT_TONE)
    if not draft or draft.status != DRAFT_CONCEPT or tone not in TONES:
        return ok()

    variant = draft.variant(tone)
    if variant is None:
        # Only the preferred tone is generated up front; produce this one on demand.
        from app.ai import get_service_for
        from app.ai.metering import record_usage
        from app.models import Organization
        from app.tasks.draft import prompt_signature_for

        org = db.session.get(Organization, msg.organization_id)
        service = get_service_for(org)
        user = msg.account.user if msg.account else None
        style = user.style_profile if user else None
        variant = service.draft(msg.sender, msg.subject, msg.body, tone, style, msg.lang, prompt_signature_for(user, account=msg.account), org=org, account=msg.account)
        record_usage(msg.organization_id, service.model_name, *service.pop_usage())
        db.session.add(DraftToneVariant(draft_id=draft.id, tone=tone, text=variant))

    draft.current_tone = tone
    draft.current_text = variant
    db.session.commit()
    return ok(text=draft.current_text)


@api_bp.route("/drafts/<int:message_id>", methods=["PUT"])
@login_required
@require_perm("editDrafts")
def edit_draft(message_id: int):
    msg = get_message(message_id)
    draft = msg.draft
    if draft:
        new_text = _body().get("text", "")
        if new_text != draft.current_text:
            db.session.add(Feedback(user_id=msg.user_id, message_id=msg.id, kind="draft_edit", from_value=draft.current_text, to_value=new_text))
        draft.current_text = new_text
        db.session.commit()
    return ok()


@api_bp.route("/drafts/<int:message_id>/regenerate", methods=["POST"])
@login_required
@require_perm("editDrafts")
def regenerate(message_id: int):
    msg = get_message(message_id)
    draft = msg.draft
    if not draft:
        return ok(error=_("geen concept")), 400
    tone = draft.current_tone or DEFAULT_TONE
    intent = (_body().get("intent") or "").strip()
    # A steered regenerate ("what I want to reply") always re-runs the model with the
    # intent — the cached tone variant would ignore it.
    variant = None if intent else draft.variant(tone)
    if variant is not None:
        draft.current_text = variant
        draft.status = DRAFT_CONCEPT
        db.session.commit()
    else:
        from app.tasks.draft import generate_draft

        generate_draft(msg.id, tone=tone, force=True, intent=intent or None)
        db.session.refresh(msg)
        draft = msg.draft
    toast = _("Concept aangepast op jouw input") if intent else _("Nieuw concept gegenereerd")
    return ok(text=draft.current_text, toast=toast)


@api_bp.route("/drafts/<int:message_id>/send", methods=["POST"])
@login_required
def send(message_id: int):
    # Explicit, user-initiated send only — gated by the sendMail permission.
    if not current_user.has_perm("sendMail"):
        abort(403)
    msg = get_message(message_id)
    draft = msg.draft
    if not draft:
        return ok(error=_("geen concept")), 400

    # If a real provider draft exists, transmit it (explicit user action). Only
    # mark the draft sent when the provider actually accepts it — otherwise the
    # user would believe the mail went out when it didn't.
    #
    # Staat "klaarzetten" uit, dan is er bewust nog geen provider-concept
    # (provider_draft_id is None). Verzenden is een expliciete actie, dus plaats het
    # concept alsnog (mét handtekening) en verstuur het dan — zonder deze stap zou de
    # knop de mail als verzonden markeren terwijl er nooit iets vertrok.
    if msg.account:
        from app.providers import get_provider

        provider = get_provider(msg.account)
        if provider.is_configured() and hasattr(provider, "send_user_draft"):
            if not draft.provider_draft_id:
                from app.tasks.writeback import write_draft

                write_draft(draft)
            if not draft.provider_draft_id:
                current_app.logger.warning("send: draft %s kon niet geplaatst worden", draft.id)
                return fail(_("Verzenden mislukt — probeer het opnieuw."), 502)
            try:
                provider.send_user_draft(draft.provider_draft_id)
            except Exception as exc:  # noqa: BLE001
                current_app.logger.warning("send failed for draft %s: %s", draft.id, exc)
                return fail(_("Verzenden mislukt — probeer het opnieuw."), 502)

    draft.status = DRAFT_SENT
    # We replied → mark the mail as awaiting their response, so it shows a status,
    # can be filtered, and the follow-up sweep can nudge it after N days.
    from app.models.base import utcnow

    msg.state = "awaiting_reply"
    msg.replied_at = utcnow()
    # Sending the reply closes the loop: mark the mail handled and tick off any task
    # linked to it — in Otto and in Microsoft To Do.
    from app.tasks.todo_sync import complete_tasks_for_message

    completed = complete_tasks_for_message(msg)
    db.session.commit()

    # Learn from what the user actually sent — keeps future drafts in their voice.
    try:
        from app.tasks.learn import learn_user_style

        learn_user_style(msg.user_id)
    except Exception:  # noqa: BLE001 — learning must never block a send
        db.session.rollback()

    flash(_("Antwoord verzonden — door jou") + (_(" · taak afgevinkt") if completed else ""), "toast")
    return ok(reload=True)


@api_bp.route("/drafts/<int:message_id>/discard", methods=["POST"])
@login_required
@require_perm("editDrafts")
def discard(message_id: int):
    msg = get_message(message_id)
    if msg.draft:
        msg.draft.status = DRAFT_DISCARDED
        db.session.commit()
    flash(_("Concept verworpen"), "toast")
    return ok(reload=True)


@api_bp.route("/drafts/<int:message_id>/force", methods=["POST"])
@login_required
@require_perm("editDrafts")
def force_draft(message_id: int):
    msg = get_message(message_id)
    # If the user gave an instruction, let Otto actually draft it (with thread context)
    # instead of dropping an empty template — a compose-from-your-words concept.
    intent = (_body().get("intent") or "").strip()
    if intent:
        from app.tasks.draft import generate_draft

        generate_draft(msg.id, force=True, intent=intent)
        db.session.refresh(msg)
        if msg.draft:
            return ok(text=msg.draft.current_text, toast=_("Concept opgesteld uit jouw input"), reload=True)
        return fail(_("Kon geen concept opstellen."), 502)
    first = (msg.sender or "daar").split()[0]
    me = (getattr(current_user, "name", "") or "").split()
    signoff = f"Groeten,\n{me[0]}" if me else "Met vriendelijke groeten"
    txt = f"Hallo {first},\n\n\n\n{signoff}"
    tone = (current_user.style_profile.tone if getattr(current_user, "style_profile", None)
            and current_user.style_profile.tone in TONES else DEFAULT_TONE)
    draft = msg.draft
    if draft is None:
        draft = Draft(message_id=msg.id, lang=msg.lang)
        db.session.add(draft)
        db.session.flush()
    for v in list(draft.tone_variants):
        db.session.delete(v)
    db.session.add(DraftToneVariant(draft_id=draft.id, tone=tone, text=txt))
    draft.status = DRAFT_CONCEPT
    draft.current_tone = tone
    draft.current_text = txt
    draft.placed = True
    db.session.commit()
    flash(_("Concept opgesteld — klaar om te bewerken"), "toast")
    return ok(reload=True)

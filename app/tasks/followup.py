"""Follow-up sweep: draft a polite nudge for sent mail that got no reply.

Runs daily from the scheduler. For every connected mailbox that enabled follow-ups, it
asks the provider for sent threads with no reply after the configured window, drafts a
reminder in the mailbox/user's voice, and stores a `FollowUp` concept. The user sends it
themselves from the Opvolgingen page — this module NEVER sends.
"""
from __future__ import annotations

from flask import current_app

from app.ai import get_service_for
from app.ai.metering import record_usage
from app.constants import DRAFT_CONCEPT, DRAFT_SENT
from app.extensions import db
from app.models import FollowUp, MailAccount, Organization
from app.tasks.draft import _MailboxStyle, preferred_tone_for, prompt_signature_for

FOLLOW_UP_WINDOW_DAYS = 30  # don't nudge threads older than N days past the trigger
FOLLOW_UP_CAP = 25          # max candidates handled per mailbox per sweep


def _followup_prefs(account: MailAccount) -> tuple[bool, int, str]:
    """(enabled, days, language) for one mailbox — from mailbox settings (shared) or the
    owner's StyleProfile (personal)."""
    if account.is_shared:
        s = account.settings
        return (
            bool(getattr(s, "follow_up_enabled", False)),
            int(getattr(s, "follow_up_days", 3) or 3),
            getattr(s, "language", None) or "antwoord in Nederlands",
        )
    owner = account.user
    style = getattr(owner, "style_profile", None) if owner else None
    return (
        bool(getattr(style, "follow_up_enabled", False)),
        int(getattr(style, "follow_up_days", 3) or 3),
        "antwoord in Nederlands",
    )


def generate_follow_ups_for_account(account: MailAccount) -> int:
    """Create follow-up concepts for one mailbox. Returns how many were created."""
    enabled, days, lang = _followup_prefs(account)
    if not enabled:
        return 0

    # Personal accounts leave organization_id NULL (derived from the owner); fall back.
    org_id = account.organization_id or (account.user.organization_id if account.user else None)
    org = db.session.get(Organization, org_id) if org_id else None
    if org is None:
        return 0

    from app.providers import get_provider

    provider = get_provider(account)
    if not provider.is_configured():
        return 0

    try:
        candidates = provider.list_awaiting_reply(days, days + FOLLOW_UP_WINDOW_DAYS, limit=FOLLOW_UP_CAP)
    except Exception:  # noqa: BLE001 — a provider hiccup must not stall the sweep
        current_app.logger.info("[followup] list_awaiting_reply failed for account %s", account.id)
        return 0

    owner = account.user
    mb = account.settings if account.is_shared else None
    style = _MailboxStyle(mb) if mb else (owner.style_profile if owner else None)
    # None = een rijke org-handtekening geldt; die plakt de provider dan zelf onder
    # het concept (create_followup_draft) in plaats van dat het model hem schrijft.
    signature = prompt_signature_for(owner, mb, account=account)
    sig_html = None
    if signature is None:
        from app.signatures import resolve_rich

        sig_html = resolve_rich(owner, account=account, mailbox_settings=mb).html
    tone = preferred_tone_for(owner, None, mb)

    service = get_service_for(org)
    made = 0
    for c in candidates:
        cid = c.get("conversation_id")
        # Don't re-nudge a thread we already have an open or sent follow-up for.
        exists = (
            db.session.query(FollowUp.id)
            .filter(
                FollowUp.account_id == account.id,
                FollowUp.provider_thread_id == cid,
                FollowUp.status.in_([DRAFT_CONCEPT, DRAFT_SENT]),
            )
            .first()
        )
        if exists:
            continue

        text = service.followup(c.get("subject", ""), c.get("body", ""), tone, style, lang, signature, org=org)
        record_usage(org.id, service.model_name, *service.pop_usage())

        draft_id = None
        # "Klaarzetten" uit (persoonlijk postvak) → de herinnering bestaat alleen in
        # Otto; bij verzenden bouwt de send-route het provider-concept alsnog op.
        place = account.is_shared or owner is None or owner.setting("klaarzetten", default=True)
        if place:
            try:
                # De kwarg alleen meegeven als er echt een handtekening is: zo blijven
                # providers (en test-fakes) zonder het argument gewoon werken.
                if sig_html:
                    draft_id = provider.create_followup_draft(c.get("provider_message_id"), text,
                                                              signature_html=sig_html)
                else:
                    draft_id = provider.create_followup_draft(c.get("provider_message_id"), text)
            except Exception:  # noqa: BLE001 — placing the provider draft is best-effort
                current_app.logger.info("[followup] create_followup_draft failed for account %s", account.id)

        db.session.add(FollowUp(
            organization_id=org.id,
            user_id=owner.id if owner else None,
            account_id=account.id,
            provider_thread_id=cid,
            provider_message_id=c.get("provider_message_id"),
            subject=c.get("subject", ""),
            to_recipients=(c.get("to") or "")[:512],
            last_sent_at=c.get("sent_at"),
            status=DRAFT_CONCEPT,
            current_text=text,
            provider_draft_id=draft_id,
        ))
        made += 1

    if made:
        db.session.commit()
    return made


def run_follow_up_sweep() -> int:
    """Sweep every connected mailbox; returns the total follow-up concepts created."""
    total = 0
    for account in db.session.query(MailAccount).filter_by(status="connected"):
        try:
            total += generate_follow_ups_for_account(account)
        except Exception:  # noqa: BLE001 — one bad mailbox must not stall the rest
            db.session.rollback()
            current_app.logger.exception("[followup] sweep failed for account %s", account.id)
    if total:
        current_app.logger.info("[followup] drafted %s follow-up(s)", total)
    return total

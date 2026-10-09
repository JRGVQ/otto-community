"""Duw openstaande afwezigheidsinstellingen naar Exchange (mailboxSettings).

Draait op de leader-elected scheduler-tick, maar doet alleen werk voor rijen met
push_state='pending' — dus goedkoop bij elke tick. Idempotent: na een geslaagde push
staat de rij op 'synced' en wordt ze pas weer opgepakt als de admin iets wijzigt.

Otto verzendt hiermee zelf niets: het configureert Exchange's eigen automatische
antwoord; Exchange stuurt het OOO-bericht (en regelt bij 'scheduled' zelf de aan/uit).
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models.base import utcnow
from app.providers import get_provider

_STATUS = {"disabled": "disabled", "scheduled": "scheduled", "always": "alwaysEnabled"}


def graph_payload(s) -> dict:
    """De Graph-automaticRepliesSetting die overeenkomt met deze rij."""
    payload = {
        "status": _STATUS.get(s.status, "disabled"),
        "externalAudience": s.external_audience or "all",
        "internalReplyMessage": s.internal_reply_html or "",
        "externalReplyMessage": s.external_reply_html or "",
    }
    if s.status == "scheduled" and s.scheduled_start and s.scheduled_end:
        payload["scheduledStartDateTime"] = {"dateTime": s.scheduled_start.isoformat(), "timeZone": "UTC"}
        payload["scheduledEndDateTime"] = {"dateTime": s.scheduled_end.isoformat(), "timeZone": "UTC"}
    return payload


def push_pending_auto_replies() -> int:
    """Duw elke 'pending' afwezigheidsinstelling naar de provider. Geeft het aantal
    geslaagde pushes terug. Nooit de scheduler laten struikelen."""
    from app.models import AutoReplySetting

    rows = db.session.query(AutoReplySetting).filter_by(push_state="pending").all()
    pushed = 0
    for s in rows:
        acct = s.account
        if acct is None:
            continue
        try:
            provider = get_provider(acct)
            if not provider.is_configured():
                continue  # demo/niet gekoppeld → blijft pending tot het kan
            if not hasattr(provider, "set_automatic_replies"):
                s.push_state = "error"
                s.last_error = "Afwezigheidsassistent wordt alleen voor Outlook ondersteund."
                db.session.commit()
                continue
            provider.set_automatic_replies(graph_payload(s))
            s.push_state = "synced"
            s.last_pushed_at = utcnow()
            s.last_error = None
            pushed += 1
        except Exception as exc:  # noqa: BLE001 — per postvak; nooit de tick breken
            db.session.rollback()
            s.push_state = "error"
            s.last_error = str(exc)[:255]
            current_app.logger.info("OOO-push overgeslagen voor account %s: %s", s.mail_account_id, exc)
        db.session.commit()
    return pushed

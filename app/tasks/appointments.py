"""Een bevestigd moment uit binnenkomende mail klaarzetten — en op verzoek boeken.

Het herkennen zit in `app/appointment.py` (geen AI, geen database). Hier staat wat er met
een vondst gebeurt: één klaargezette afspraak per gesprek, wachtend op de gebruiker. En het
boeken zelf, dat pas gebeurt als de gebruiker in de app op "Zet in agenda" klikt — Otto
boekt nooit uit zichzelf.
"""
from __future__ import annotations

from flask import current_app

from app import appointment as detect
from app.extensions import db
from app.models import StagedAppointment


def _timezone():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(current_app.config.get("APP_TIMEZONE") or "Europe/Brussels")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Europe/Brussels")


def stage_from_message(message) -> bool:
    """Zet een afspraak klaar als deze mail een moment bevestigt. Idempotent per gesprek.

    Geeft True terug als er iets is klaargezet. Best-effort: klaarzetten is een bonus,
    het mag de verwerking van de mail nooit breken."""
    account = getattr(message, "account", None)
    if account is None or not detect.enabled_for(account):
        return False
    if getattr(message, "received_at", None) is None:
        return False
    try:
        received = message.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=_timezone())
        vondst = detect.detect(message.subject, message.body, received, _timezone())
    except Exception:  # noqa: BLE001
        return False
    if vondst is None:
        return False

    cid = (message.provider_thread_id or "").strip()
    # Al een afspraak klaargezet (of geboekt) voor dit gesprek? Dan niets — anders zet elke
    # ronde van de klant er een bij. Weggegooide afspraken tellen niet: die mag de klant
    # met een nieuw voorstel opnieuw oproepen.
    if cid:
        bestaat = (
            db.session.query(StagedAppointment.id)
            .filter_by(mail_account_id=account.id, provider_thread_id=cid)
            .filter(StagedAppointment.status.in_(("pending", "confirmed")))
            .first()
        )
        if bestaat:
            return False

    owner_id = getattr(message, "assignee_user_id", None) or account.user_id
    db.session.add(StagedAppointment(
        mail_account_id=account.id,
        organization_id=account.organization_id or getattr(message, "organization_id", None),
        user_id=owner_id,
        provider_thread_id=cid or None,
        source_message_id=getattr(message, "id", None),
        subject=(message.subject or "")[:512],
        counterparty=(getattr(message, "sender", None) or message.sender_email or "")[:255],
        start_at=vondst["start"],
        duration_min=_duration_for(account),
        status="pending",
    ))
    db.session.commit()
    current_app.logger.info("[afspraak] moment klaargezet voor account %s", account.id)
    return True


def _duration_for(account) -> int:
    from app.scheduling import duration_for
    try:
        return duration_for(account)
    except Exception:  # noqa: BLE001
        return detect.DEFAULT_DURATION_MIN


def book(staged: StagedAppointment) -> bool:
    """Boek een klaargezette afspraak echt in de agenda. Alleen vanuit de bevestig-actie.

    Geeft True terug als het item is aangemaakt. Zet `event_id` en status 'confirmed'."""
    account = staged.account
    if account is None:
        return False
    from app.providers import get_provider

    provider = get_provider(account)
    if not (provider.is_configured() and hasattr(provider, "create_event")):
        return False
    event_id = provider.create_event(
        subject=staged.subject or "Afspraak",
        start=staged.start_at,
        duration_min=staged.duration_min or detect.DEFAULT_DURATION_MIN,
        attendee_email=_counterparty_email(staged),
        attendee_name=staged.counterparty,
    )
    if not event_id:
        return False
    staged.event_id = event_id
    staged.status = "confirmed"
    db.session.commit()
    return True


def _counterparty_email(staged: StagedAppointment) -> str | None:
    """Het e-mailadres van de tegenpartij, uit het bronbericht als dat er nog is."""
    from app.models import Message

    if staged.source_message_id:
        msg = db.session.get(Message, staged.source_message_id)
        if msg and msg.sender_email:
            return msg.sender_email
    return None

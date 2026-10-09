"""Post shared-mailbox events to a Microsoft Teams channel via an Incoming Webhook.

The admin pastes a Teams "Incoming Webhook" URL on the mailbox settings; Otto then
POSTs a MessageCard when actionable mail arrives or a mail is assigned. Best-effort:
no URL / notifications off / network error → silent no-op, never blocks the pipeline.
"""
from __future__ import annotations

import json
import urllib.request

from flask import current_app

_THEME = "5B5FC7"  # Otto / Teams purple


def _post(url: str, payload: dict) -> bool:
    """POST a card to the webhook. Returns True on success (best-effort)."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=6)  # noqa: S310 — admin-provided Teams URL
        return True
    except Exception as exc:  # noqa: BLE001 — notifications must never break the app
        current_app.logger.info("Teams webhook post skipped: %s", exc)
        return False


def _card(title: str, text: str, facts: list[tuple[str, str]] | None = None) -> dict:
    section = {"activityTitle": title, "text": text}
    if facts:
        section["facts"] = [{"name": k, "value": v} for k, v in facts if v]
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _THEME,
        "summary": title,
        "sections": [section],
    }


def _notifiable(account):
    """The mailbox settings if this shared mailbox should post to Teams, else None."""
    if not (account and account.is_shared):
        return None
    s = account.settings
    return s if (s and s.teams_notify and s.teams_webhook_url) else None


def notify_new_mail(message) -> bool:
    """A new mail that needs handling arrived in a shared mailbox.

    Staat bundelen aan, dan gaat de melding de wachtrij in en post `flush_bundles` ze
    later als één kaart. Anders — het oude gedrag — meteen één kaart."""
    s = _notifiable(message.account)
    if not s:
        return False
    if s.teams_bundle:
        return _enqueue(message, s)
    facts = [
        ("Van", message.sender or message.sender_email or "—"),
        ("Categorie", message.category.name if message.category else "—"),
        ("Postvak", message.account.target_address or ""),
    ]
    return _post(s.teams_webhook_url, _card(
        f"📬 Nieuwe mail in {s.name}", f"**{message.subject or '(geen onderwerp)'}**", facts))


def _enqueue(message, settings) -> bool:
    """Leg een momentopname van deze mail in de wachtrij. Best-effort, zoals de post zelf:
    lukt het niet, dan mist het kanaal één melding — dat mag de pijplijn niet stoppen."""
    from app.extensions import db
    from app.models import TeamsPending

    try:
        db.session.add(TeamsPending(
            mail_account_id=message.account_id,
            subject=(message.subject or "")[:512],
            sender=(message.sender or message.sender_email or "")[:255],
            category_name=(message.category.name if message.category else None),
        ))
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.info("Teams-bundel enqueue overgeslagen: %s", exc)
        return False


def flush_bundles() -> int:
    """Leeg de wachtrij van elke gedeelde mailbox waarvan een vast moment gepasseerd is.

    Draait elke scheduler-tik en self-gate't op het moment + het tijdstip van de vorige
    flush (zie app/teams_bundle.py) — precies zoals de dagelijkse takenmail. Geeft terug
    hoeveel kaarten er verstuurd zijn."""
    from app.extensions import db
    from app.models import MailboxSettings, TeamsPending
    from app.models.base import utcnow
    from app import teams_bundle

    verstuurd = 0
    now = utcnow()
    rijen = (
        db.session.query(MailboxSettings)
        .filter(MailboxSettings.teams_notify.is_(True),
                MailboxSettings.teams_bundle.is_(True),
                MailboxSettings.teams_webhook_url.isnot(None))
        .all()
    )
    for s in rijen:
        hours = teams_bundle.parse_hours(s.teams_bundle_hours)
        # Nog nooit geflusht? Zet de klok nu, zodat het eerstvolgende moment het startpunt
        # is en er geen berg oude meldingen in één keer uit gaat.
        if s.teams_last_flush_at is None:
            s.teams_last_flush_at = now
            db.session.commit()
            continue
        if not teams_bundle.due(now, hours, s.teams_last_flush_at):
            continue
        pending = (
            db.session.query(TeamsPending)
            .filter_by(mail_account_id=s.mail_account_id)
            .order_by(TeamsPending.id)
            .all()
        )
        if pending:
            items = [{"subject": p.subject, "sender": p.sender,
                      "category_name": p.category_name} for p in pending]
            if _post(s.teams_webhook_url, teams_bundle.summary_card(s.name, items)):
                for p in pending:
                    db.session.delete(p)
                verstuurd += 1
            # Mislukt de post, dan laten we de wachtrij staan voor de volgende ronde;
            # alleen het flush-moment schuift op zodat we niet elke tik opnieuw proberen.
        s.teams_last_flush_at = now
        db.session.commit()
    return verstuurd


def notify_assignment(message, assignee_name: str) -> bool:
    """A mail was assigned to a teammate."""
    s = _notifiable(message.account)
    if not s:
        return False
    return _post(s.teams_webhook_url, _card(
        f"👤 Toegewezen in {s.name}",
        f"**{message.subject or '(geen onderwerp)'}** → {assignee_name}"))

"""Toezeggingen uit verzonden mail omzetten in taken.

Draait dagelijks vanuit de scheduler, naast de opvolgsweep. Die kijkt naar verzonden mail
die géén antwoord kreeg; deze kijkt naar wat je in die mail beloofd hebt — ongeacht of er
al geantwoord is, want een belofte blijft staan.

Het herkennen zit in `app/commitments.py` en gebruikt geen AI: een belofte plus een
termijn in dezelfde zin. Hier staat alleen wat er met een vondst gebeurt, en vooral wat
er níét mag gebeuren — dezelfde toezegging twee keer op je lijst zetten.
"""
from __future__ import annotations

from flask import current_app

from app import commitments as detect
from app.extensions import db
from app.models import MailAccount, Task
from app.models.base import utcnow

LOOKBACK_DAYS = 3   # de sweep draait dagelijks; drie dagen dekt een gemiste run ruim
SENT_CAP = 40       # per postvak per ronde — genoeg voor een drukke dag, geen marathon


def _due_display(due) -> str:
    """Zoals de takenlijst het toont: "Vandaag", "Morgen", anders "wo 26 aug"."""
    from datetime import timedelta

    vandaag = utcnow().date()
    if due == vandaag:
        return "Vandaag"
    if due == vandaag + timedelta(days=1):
        return "Morgen"
    dagen = ("ma", "di", "wo", "do", "vr", "za", "zo")
    maanden = ("jan", "feb", "mrt", "apr", "mei", "jun",
               "jul", "aug", "sep", "okt", "nov", "dec")
    return f"{dagen[due.weekday()]} {due.day} {maanden[due.month - 1]}"


def commitments_for_account(account: MailAccount) -> int:
    """Maak taken uit de toezeggingen in het recent verzonden mail van dit postvak."""
    if not detect.enabled_for(account):
        return 0
    owner = account.user
    if owner is None:
        return 0   # zonder eigenaar is er niemand om de taak aan te geven

    from app.providers import get_provider

    provider = get_provider(account)
    if not (provider.is_configured() and hasattr(provider, "list_recent_sent")):
        return 0
    try:
        verzonden = provider.list_recent_sent(days=LOOKBACK_DAYS, limit=SENT_CAP)
    except Exception:  # noqa: BLE001 — een hapering bij de provider mag de sweep niet stoppen
        current_app.logger.info("[toezeggingen] verzonden mail ophalen mislukt voor %s", account.id)
        return 0

    org_id = account.organization_id or owner.organization_id
    gemaakt = 0
    for mail in verzonden:
        pmid = mail.get("provider_message_id")
        sent_at = mail.get("sent_at")
        if not (pmid and sent_at):
            continue
        # Al eens uit deze mail geoogst? Dan niets meer — anders staat elke toezegging
        # er na drie sweeps drie keer op, en dat is precies hoe je een takenlijst sloopt.
        if db.session.query(Task.id).filter_by(source_sent_message_id=pmid).first():
            continue

        for vondst in detect.find(mail.get("body"), sent_at.date()):
            db.session.add(Task(
                organization_id=org_id, user_id=owner.id,
                title=vondst["text"][:255],
                context=(mail.get("subject") or "")[:255],
                due_date=vondst["due"], due_display=_due_display(vondst["due"]),
                linked_subject=(mail.get("subject") or "")[:512],
                source_sent_message_id=pmid,
                list_name="Werk",
            ))
            gemaakt += 1
        # Ook zonder vondst een merkteken achterlaten zou netter zijn voor de teller, maar
        # dat vraagt een lege taak; in plaats daarvan kost een mail zonder toezegging
        # alleen het opnieuw doorlopen van dezelfde zinnen — dat is gratis.
    if gemaakt:
        db.session.commit()
    return gemaakt


def run_commitment_sweep() -> int:
    """Alle gekoppelde postvakken. Geeft terug hoeveel taken er bij kwamen."""
    totaal = 0
    for account in db.session.query(MailAccount).filter_by(status="connected"):
        try:
            totaal += commitments_for_account(account)
        except Exception:  # noqa: BLE001 — één postvak mag de rest niet ophouden
            db.session.rollback()
            current_app.logger.exception("[toezeggingen] sweep mislukt voor postvak %s", account.id)
    if totaal:
        current_app.logger.info("[toezeggingen] %s taak/taken aangemaakt", totaal)
    return totaal

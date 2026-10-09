"""Actiepunten uit vergadertranscripties omzetten in taken op de teamlijst.

Het herkennen zit in `app/transcripts.py` (geen AI, geen database). Hier staat wat er met
een vondst gebeurt: een taak per actiepunt in de lijst "Vergadering", met de naam van de
verantwoordelijke erbij, en — cruciaal — hoogstens één keer. De sweep draait dagelijks, dus
zonder een merkteken per actiepunt zou elke ronde dezelfde punten opnieuw toevoegen.

Otto boekt of verstuurt niets: de actiepunten komen op de takenlijst (die naar Microsoft
To Do/Planner spiegelt), waar het team ze zelf oppakt.
"""
from __future__ import annotations

from flask import current_app

from app import transcripts as detect
from app.extensions import db
from app.models import MailAccount, Task

LOOKBACK_DAYS = 3
MEETING_LIST = "Vergadering"


def _due_display(due) -> str:
    from datetime import timedelta

    from app.models.base import utcnow

    if due is None:
        return ""
    vandaag = utcnow().date()
    if due == vandaag:
        return "Vandaag"
    if due == vandaag + timedelta(days=1):
        return "Morgen"
    dagen = ("ma", "di", "wo", "do", "vr", "za", "zo")
    maanden = ("jan", "feb", "mrt", "apr", "mei", "jun",
               "jul", "aug", "sep", "okt", "nov", "dec")
    return f"{dagen[due.weekday()]} {due.day} {maanden[due.month - 1]}"


def items_to_tasks(account: MailAccount, transcript: dict) -> int:
    """Maak taken uit de actiepunten in één transcriptie. Idempotent per transcriptie.

    `transcript` = {"id", "subject", "date", "text"}. Geeft terug hoeveel taken erbij kwamen.
    """
    owner = account.user
    if owner is None:
        return 0
    tid = transcript.get("id")
    if not tid:
        return 0
    items = detect.extract_action_items(transcript.get("text"), transcript.get("date"))
    org_id = account.organization_id or owner.organization_id
    onderwerp = (transcript.get("subject") or "Vergadering")[:255]
    gemaakt = 0
    for idx, item in enumerate(items):
        merk = f"transcript:{tid}:{idx}"
        # Al eens uit dit actiepunt een taak gemaakt? Dan niets — anders staat het na drie
        # sweeps drie keer op de lijst.
        if db.session.query(Task.id).filter_by(source_sent_message_id=merk).first():
            continue
        context = onderwerp
        if item.get("owner"):
            context = f"{onderwerp} — {item['owner']}"
        db.session.add(Task(
            organization_id=org_id, user_id=owner.id,
            title=item["text"][:255],
            context=context[:255],
            due_date=item.get("due"), due_display=_due_display(item.get("due")),
            linked_subject=onderwerp[:512],
            source_sent_message_id=merk,
            list_name=MEETING_LIST,
        ))
        gemaakt += 1
    if gemaakt:
        db.session.commit()
    return gemaakt


def transcripts_for_account(account: MailAccount) -> int:
    """Haal recente transcripties op en zet hun actiepunten op de lijst."""
    if not detect.enabled_for(account):
        return 0
    from app.providers import get_provider

    provider = get_provider(account)
    if not (provider.is_configured() and hasattr(provider, "list_recent_transcripts")):
        return 0
    try:
        transcripties = provider.list_recent_transcripts(days=LOOKBACK_DAYS)
    except Exception:  # noqa: BLE001 — een hapering bij de provider mag de sweep niet stoppen
        current_app.logger.info("[transcripties] ophalen mislukt voor postvak %s", account.id)
        return 0
    totaal = 0
    for t in transcripties or []:
        try:
            totaal += items_to_tasks(account, t)
        except Exception:  # noqa: BLE001 — één transcriptie mag de rest niet ophouden
            db.session.rollback()
    return totaal


def run_transcript_sweep() -> int:
    """Alle gekoppelde postvakken. Geeft terug hoeveel taken er bij kwamen."""
    totaal = 0
    for account in db.session.query(MailAccount).filter_by(status="connected"):
        try:
            totaal += transcripts_for_account(account)
        except Exception:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.exception("[transcripties] sweep mislukt voor postvak %s", account.id)
    if totaal:
        current_app.logger.info("[transcripties] %s taak/taken aangemaakt", totaal)
    return totaal

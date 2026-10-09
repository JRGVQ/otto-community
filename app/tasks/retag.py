"""Bestaande Outlook-mail her-taggen na een label-naamswijziging.

Graph kan een hoofdcategorie niet hernoemen (alleen de kleur is muteerbaar). Wanneer
een categorienaam verandert — nummering aan/uit, of een hernoeming — moet bestaande
mail dus expliciet worden herschreven van de oude naam naar de nieuwe, anders blijft
het oude label (grijs, want uit de categorielijst verwijderd) naast het nieuwe staan.

Draait via enqueue (inline zonder Redis, Celery mét) en is volledig best-effort:
een mailbox die niet meewerkt mag de rest nooit blokkeren. Per oude naam gecapt —
de write-back zet een gemiste straggler alsnog recht zodra die mail opnieuw door de
pijplijn komt.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import MailAccount


def retag_account_labels(account_id: int, mapping: dict[str, tuple[str, str | None]]) -> int:
    """Herschrijf voor één mailbox elke oude labelnaam naar de juiste vorm.

    `mapping`: {oude labelnaam → (juiste labelnaam, kleur)}. Geeft terug hoeveel
    berichten zijn aangepast."""
    account = db.session.get(MailAccount, account_id)
    if not account or account.provider != "outlook" or account.is_mock:
        return 0
    from app.outlook_labels import base_names
    from app.providers import get_provider

    try:
        provider = get_provider(account)
        if not provider.is_configured() or not hasattr(provider, "retag_messages"):
            return 0
        org = account.organization or (account.user.organization if account.user else None)
        bases = base_names(org) if org else set()
        done = 0
        for old, (new, color) in mapping.items():
            try:
                done += provider.retag_messages(old, new, color=color, replace_bases=bases)
            except Exception as exc:  # noqa: BLE001 — één naam mag de rest niet stoppen
                current_app.logger.info("retag '%s' overgeslagen voor %s: %s", old, account.email, exc)
        if done:
            current_app.logger.info("[retag] %s bericht(en) hergelabeld in %s", done, account.email)
        return done
    except Exception as exc:  # noqa: BLE001 — best-effort, nooit de aanroeper breken
        current_app.logger.info("retag overgeslagen voor account %s: %s", account_id, exc)
        return 0


def retag_org_labels(org, mapping: dict[str, tuple[str, str | None]]) -> None:
    """Zet de her-tag klaar voor elke gekoppelde Outlook-mailbox van de organisatie."""
    if not mapping:
        return
    from app.models import User
    from app.tasks.enqueue import enqueue

    # Persoonlijke mailboxen laten organization_id soms NULL (afgeleid via de
    # eigenaar), dus beide routes tellen: het account zelf óf zijn gebruiker.
    accounts = (
        db.session.query(MailAccount)
        .outerjoin(User, MailAccount.user_id == User.id)
        .filter(
            MailAccount.provider == "outlook",
            MailAccount.status == "connected",
            (MailAccount.organization_id == org.id) | (User.organization_id == org.id),
        )
        .all()
    )
    for account in accounts:
        if not account.is_mock:
            enqueue(retag_account_labels, account.id, mapping)

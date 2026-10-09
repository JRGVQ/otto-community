"""Bestaande mail opnieuw categoriseren — én leeg-opgeslagen berichten herstellen.

De 'alles opnieuw categoriseren'-knop laat een gebruiker zijn eigen inbox opnieuw laten
sorteren, zonder support. Twee dingen gebeuren per bericht:

  1. Herstel — een bericht dat door de smalle-cursor-bug (PR #112) leeg is opgeslagen
     (geen afzender én geen body) krijgt zijn echte inhoud alsnog van de provider.
  2. Herclassificatie — categorie (en, als de gebruiker dat wil, het bijbehorende
     concept) wordt gewist en het bericht gaat opnieuw door de pijplijn.

Begrensd en nieuwste-eerst: een reusachtige inbox hoort niet in één request. De rest
volgt bij een volgende keer of blijft handmatig sorteerbaar.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import MailAccount, Message


def _looks_broken(msg: Message) -> bool:
    """Het symptoom van de smalle-cursor-bug: id-only opgeslagen, dus geen afzender en
    geen body. (Een echte, lege mail heeft dat vrijwel nooit allebei tegelijk.)"""
    return not (msg.sender_email or "").strip() and not (msg.body or "").strip()


def _repair_content(account: MailAccount, msg: Message, provider) -> bool:
    """Vul een leeg-opgeslagen bericht opnieuw met de echte inhoud van de provider.
    Geeft terug of er iets veranderde."""
    if not (msg.provider_message_id and hasattr(provider, "fetch_message")):
        return False
    dto = provider.fetch_message(msg.provider_message_id)
    if dto is None:
        return False
    changed = False
    for field, value in (("sender", dto.sender), ("sender_email", dto.sender_email),
                         ("subject", dto.subject), ("snippet", dto.snippet), ("body", dto.body)):
        if value and value != getattr(msg, field, None):
            setattr(msg, field, value)
            changed = True
    if dto.body_html and dto.body_html != msg.body_html:
        msg.body_html = dto.body_html
        changed = True
    if dto.sender:
        msg.initials = (dto.sender[:2] or "?").upper()
    return changed


def recategorize_account(account_id: int, limit: int = 500) -> int:
    """Herstel + herclassificeer de (persoonlijke) inbox van één postvak.

    Retourneert hoeveel berichten opnieuw in de pijplijn zijn gezet."""
    account = db.session.get(MailAccount, account_id)
    if not account or account.is_mock:
        return 0
    from app.tasks.enqueue import enqueue
    from app.tasks.pipeline import process_incoming

    msgs = (
        db.session.query(Message)
        .filter(Message.account_id == account.id,
                Message.is_seed.is_(False),
                Message.archived.is_(False))
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    if not msgs:
        return 0

    provider = None
    broken = [m for m in msgs if _looks_broken(m)]
    if broken:
        try:
            from app.providers import get_provider

            provider = get_provider(account)
            if not provider.is_configured():
                provider = None
        except Exception as exc:  # noqa: BLE001 — zonder provider slaan we het herstel over
            current_app.logger.info("[recat] provider niet beschikbaar voor %s: %s", account.email, exc)
            provider = None

    repaired = 0
    for msg in msgs:
        if provider is not None and msg in broken and _repair_content(account, msg, provider):
            repaired += 1
        # Wis de categorie zodat de pijplijn opnieuw classificeert (idempotentie-gate in
        # process_incoming valt anders meteen terug op de oude, foute categorie).
        msg.category_id = None
        msg.subcategory_id = None
    db.session.commit()

    for msg in msgs:
        enqueue(process_incoming, msg.id)
    if repaired:
        current_app.logger.info("[recat] %s bericht(en) hersteld in %s", repaired, account.email)
    return len(msgs)


def recategorize_user(user, limit_per_box: int = 500) -> int:
    """Herstel + herclassificeer alle persoonlijke postvakken van een gebruiker.
    Gedeelde mailboxen blijven buiten schot — die horen bij het team, niet bij één persoon."""
    total = 0
    for acct in user.mail_accounts:
        if acct.is_shared or acct.is_mock:
            continue
        total += recategorize_account(acct.id, limit=limit_per_box)
    return total

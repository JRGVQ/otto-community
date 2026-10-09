"""Categorisation task: LLM/stub classification + label write-back."""
from __future__ import annotations

from flask import current_app

from app.ai import get_service_for
from app.ai.metering import record_usage
from app.extensions import db
from app.models import Category, Message, Organization
from app.tasks.writeback import write_label


def _auto_folder_enabled(message: Message) -> bool:
    """Whether this mailbox opted in to moving categorised mail out of the inbox."""
    acct = message.account
    if acct is None:
        return False
    if acct.is_shared:
        return bool(getattr(acct.settings, "auto_folder_enabled", False))
    owner = acct.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "auto_folder_enabled", False))


def _auto_move(message: Message, category: Category) -> None:
    """Move mail in a 'move out of inbox' category into its target folder.

    Opt-in per mailbox; only acts when the category is explicitly not kept in the inbox
    and names a target folder. Shared mailboxes honour their own per-category override.
    Best-effort — categorisation must never fail on a move."""
    if message.archived or not _auto_folder_enabled(message) or not message.provider_message_id:
        return
    acct = message.account
    if acct is not None and acct.is_shared:
        from app.models.mailbox_category import effective_foldering

        keep_in_inbox, target_folder = effective_foldering(acct, category)
    else:
        keep_in_inbox, target_folder = bool(category.keep_in_inbox), category.target_folder
    if keep_in_inbox or not target_folder:
        return
    try:
        from app.providers import get_provider

        provider = get_provider(message.account)
        if not provider.is_configured():
            return
        folder_id = provider.ensure_folder(target_folder)
        if not folder_id:
            return
        provider.move_to_folder(message.provider_message_id, folder_id)
        message.archived = True
    except Exception:  # noqa: BLE001 — a failed move must not break categorisation
        current_app.logger.info("[categorize] auto-move skipped for message %s", message.id)


def _classify_as_noise(message: Message) -> dict | None:
    """Bulkmail rechtstreeks in de ruiscategorie, zónder de LLM aan te roepen.

    Dit is het hele punt van het ruisfilter: een nieuwsbrief kostte tot nu toe een volle
    classificatie (en soms een concept) om vervolgens ongelezen weg te gaan. De
    List-Unsubscribe-header die Otto al binnenhaalt zegt hetzelfde, gratis en met meer
    zekerheid dan een model kan bieden.

    Geeft het resultaatdict terug als het bericht als ruis is weggezet, anders None —
    dan gaat het gewoon de normale weg."""
    from app.noise import classify_as_noise

    category = classify_as_noise(message)
    if category is None:
        return None
    message.category_id = category.id
    message.classified_by = "filter"  # zonder AI-aanroep — telt als besparing in het ruis-rapport
    db.session.flush()
    write_label(message)
    _auto_move(message, category)
    db.session.commit()
    # `confidence` is hier geen inschatting maar een vaststelling: de afzender zegt zelf
    # dat dit een lijstmail is. `sensitive` blijft False — ruis vraagt geen extra blik.
    return {"category_key": category.key, "confidence": 100, "sensitive": False, "source": "noise"}


def _classify_by_directory(message: Message) -> dict | None:
    """CRM-standaardcategorie: mail van een contact/domein met een standaardcategorie
    gaat daar deterministisch in — zonder AI-aanroep. Contact wint van bedrijf, en dit
    wint van de AI (bewuste keuze van de gebruiker). Enterprise/Teams-functie."""
    from app.directory import default_category_for

    category = default_category_for(message)
    if category is None:
        return None
    message.category_id = category.id
    message.classified_by = "directory"
    db.session.flush()
    write_label(message)
    _auto_move(message, category)
    db.session.commit()
    return {"category_key": category.key, "confidence": 100, "sensitive": False, "source": "directory"}


def categorize_message(message_id: int) -> dict | None:
    message = db.session.get(Message, message_id)
    if not message:
        return None

    # Passief het CRM-adresboek bijwerken (Teams/Enterprise; best-effort, no-op elders).
    try:
        from app.directory import collect_from_message

        collect_from_message(message)
    except Exception:  # noqa: BLE001 — CRM mag de classificatie nooit breken
        db.session.rollback()

    # 1) Een expliciete CRM-standaardcategorie wint van alles (ook van het ruisfilter):
    #    de gebruiker zei zelf "mail van deze relatie hoort hier".
    directory = _classify_by_directory(message)
    if directory is not None:
        return directory

    noise = _classify_as_noise(message)
    if noise is not None:
        return noise

    categories = (
        db.session.query(Category)
        .filter_by(organization_id=message.organization_id)
        .order_by(Category.position)
        .all()
    )

    # Shared mailbox → only its enabled categories are offered to the classifier, and
    # each category's instruction is augmented with the mailbox's AI override (fase 3).
    acct = message.account
    if acct is not None and acct.is_shared:
        from app.models.mailbox_category import effective_config

        cat_dicts = []
        for c in categories:
            enabled, _draft, ai_extra = effective_config(acct, c)
            if not enabled:
                continue
            ai = ((c.ai or "") + ("\n" + ai_extra if ai_extra else "")).strip()
            cat_dicts.append({"key": c.key, "name": c.name, "ai": ai})
        if not cat_dicts:  # safety: never leave the classifier with nothing
            cat_dicts = [{"key": c.key, "name": c.name, "ai": c.ai} for c in categories]
    else:
        cat_dicts = [{"key": c.key, "name": c.name, "ai": c.ai} for c in categories]

    org = db.session.get(Organization, message.organization_id)
    service = get_service_for(org)
    result = service.categorize(message.subject or "", message.sender_email or "", message.body or "", cat_dicts)
    record_usage(message.organization_id, service.model_name, *service.pop_usage())
    chosen = next((c for c in categories if c.key == result["category_key"]), None)
    if chosen:
        message.category_id = chosen.id
        message.classified_by = "ai"
        db.session.flush()
        write_label(message)
        _auto_move(message, chosen)
        db.session.commit()
    return result


def recategorize_account(account_id: int, limit: int = 200) -> int:
    """Laat de AI de recentste inbox-berichten van één postvak opnieuw indelen.

    Voor de "Opnieuw categoriseren"-knop: na een haperende import (mails zonder
    afzender/inhoud, inmiddels hersteld) klopt de eerdere indeling vaak niet meer.
    Slaat over wat de gebruiker zélf heeft ingedeeld (classified_by="user") — die
    keuze wint altijd van de AI. Best-effort per bericht: één mislukking (AI-budget,
    Graph-label) stopt de rest niet. Geeft het aantal opnieuw ingedeelde mails terug."""
    messages = (
        db.session.query(Message)
        .filter(
            Message.account_id == account_id,
            Message.is_seed.is_(False),
            Message.archived.is_(False),
            db.or_(Message.classified_by.is_(None), Message.classified_by != "user"),
        )
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    done = 0
    for msg in messages:
        try:
            categorize_message(msg.id)
            done += 1
        except Exception as exc:  # noqa: BLE001 — hercategoriseren mag nooit halverwege stranden
            db.session.rollback()
            current_app.logger.info("[hercategorisatie] bericht %s overgeslagen: %s", msg.id, exc)
    return done

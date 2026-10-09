"""Write categorisation labels and concept drafts back to the provider.

All calls degrade silently when the account's provider isn't configured (demo mode)
so categorisation/draft generation still complete locally.
"""
from __future__ import annotations

from flask import current_app

from app.providers import get_provider
from app.providers.exceptions import ProviderError


def write_label(message) -> None:
    if not (message.account and message.provider_message_id and message.category):
        return
    try:
        from app.outlook_labels import base_names, label_for

        provider = get_provider(message.account)
        if provider.is_configured():
            # De naam kan genummerd zijn ("1. Te beantwoorden") wanneer de organisatie
            # dat aan heeft staan — zie app/outlook_labels.py. replace_bases laat de
            # provider ANDERE Otto-labels van het bericht halen (max. één categorie);
            # de eigen tags van de gebruiker blijven onaangeroerd.
            org = message.category.organization
            name = label_for(message.category, org)
            try:
                provider.apply_label(message.provider_message_id, name,
                                     color=message.category.color, replace_bases=base_names(org))
            except TypeError:  # provider (of test-fake) zonder het nieuwe argument
                provider.apply_label(message.provider_message_id, name, color=message.category.color)
    except (ProviderError, Exception) as exc:  # never let write-back break the pipeline
        current_app.logger.info("Label write-back skipped: %s", exc)


def _org_signature(message) -> tuple[str | None, str | None]:
    """(signature_html, signature_text) voor de write-back, of (None, None).

    Alleen gevuld wanneer een rijke (HTML) org-handtekening geldt — anders zit de
    handtekening al in de conceptekst zelf en hoort de provider niets te plakken.
    Best-effort: een fout hier mag een concept nooit tegenhouden."""
    try:
        from app.signatures import resolve_rich

        account = message.account
        user = account.user if account else None
        org = getattr(user, "organization", None) if user else None
        mode, marker = _cloud_mode(org)
        # Cloud-modus: de Exchange transport-regel zet de handtekening server-side, dus
        # Otto plakt zelf niets in het concept (anders zou hij dubbel komen).
        if mode == "cloud":
            return None, None
        mb = account.settings if (account and account.is_shared) else None
        resolved = resolve_rich(user, account=account, mailbox_settings=mb, message=message)
        if resolved.html:
            html = resolved.html
            # Combo-modus: verborgen marker erbij, zodat de transport-regel deze (al
            # getekende) mail overslaat en niet nóg een handtekening toevoegt.
            if mode == "combo":
                from app.cloud_signature import otto_marker_html

                html += otto_marker_html(marker)
            return html, resolved.text
    except Exception as exc:  # noqa: BLE001
        current_app.logger.info("Handtekening bepalen overgeslagen: %s", exc)
    return None, None


def _cloud_mode(org) -> tuple[str, str]:
    """(modus, marker) van de organisatie: 'client' (standaard), 'cloud' of 'combo'."""
    from app.cloud_signature import DEFAULT_MARKER

    org_id = getattr(org, "id", None)
    if not org_id:
        return "client", DEFAULT_MARKER
    try:
        from app.extensions import db
        from app.models import CloudSignatureSetting

        row = db.session.query(CloudSignatureSetting).filter_by(organization_id=org_id).first()
    except Exception:  # noqa: BLE001 — tabel nog niet gemigreerd
        return "client", DEFAULT_MARKER
    if row is None:
        return "client", DEFAULT_MARKER
    return (row.mode or "client"), (row.marker or DEFAULT_MARKER)


def remove_placed_draft(draft) -> None:
    """Haal Otto's concept uit de Concepten-map en vergeet het provider-id.

    Gebruikt wanneer "klaarzetten" uit staat: zonder dit zou een eerder geplaatst
    concept met verouderde tekst in Outlook blijven staan — en zou de Verzenden-knop
    (die op provider_draft_id rijdt) die oude versie versturen. Best-effort opruimen;
    het id wissen is het essentiële deel en gebeurt altijd."""
    message = draft.message
    draft.placed = False
    try:
        if message and message.account:
            provider = get_provider(message.account)
            if provider.is_configured():
                conversation = message.provider_thread_id or (
                    message.thread.provider_thread_id if message.thread else None
                )
                stale = {draft.provider_draft_id} if draft.provider_draft_id else set()
                if hasattr(provider, "find_own_drafts"):
                    stale.update(provider.find_own_drafts(conversation))
                for old_id in filter(None, stale):
                    try:
                        provider.delete_draft(old_id)
                    except Exception as exc:  # noqa: BLE001 — al weg is ook goed
                        current_app.logger.info("Concept opruimen overgeslagen: %s", exc)
    except (ProviderError, Exception) as exc:  # noqa: BLE001 — nooit de pijplijn breken
        current_app.logger.info("Concept opruimen overgeslagen: %s", exc)
    draft.provider_draft_id = None


def write_draft(draft) -> str | None:
    """Zet (of vervang) het concept in de Concepten-map. Verzendt nooit.

    Eén concept per mail, ook als er onderweg iets misgaat. Dat vroeg twee dingen die er
    eerder niet waren:

    1. Het id wordt meteen na het aanmaken vastgelegd en gecommit. Voorheen gebeurde dat
       pas na een volledig geslaagde ronde, dus een fout halverwege liet een concept in
       Outlook achter dat Otto niet kende — en de volgende ronde maakte er gewoon nóg een.
    2. Vóór het aanmaken worden Otto's eerdere concepten in dezelfde conversatie
       opgezocht en verwijderd, niet alleen het id dat wij toevallig bewaarden. Zo worden
       ook die weesconcepten van eerdere mislukkingen alsnog opgeruimd.

    Alleen concepten met Otto's eigen merkteken worden verwijderd, dus een concept dat de
    gebruiker zelf schreef blijft altijd staan.

    Geldt er een rijke (HTML) org-handtekening, dan gaat die hier ALS HTML mee naar de
    provider — de conceptekst zelf bevat hem dan niet (zie prompt_signature_for). Zo is
    de handtekening gegarandeerd en pixel-perfect, wat het model ook schreef.
    """
    from app.extensions import db

    message = draft.message
    if not (message and message.account):
        return None
    try:
        provider = get_provider(message.account)
        if not provider.is_configured():
            return None

        conversation = message.provider_thread_id or (
            message.thread.provider_thread_id if message.thread else None
        )
        # Opruimen: het bewaarde id plus alles wat Otto in deze conversatie liet staan.
        stale = {draft.provider_draft_id} if draft.provider_draft_id else set()
        if hasattr(provider, "find_own_drafts"):
            stale.update(provider.find_own_drafts(conversation))
        for old_id in filter(None, stale):
            try:
                provider.delete_draft(old_id)
            except Exception as exc:  # noqa: BLE001 — al weg is ook goed
                current_app.logger.info("Oud concept verwijderen overgeslagen: %s", exc)
        if draft.provider_draft_id:
            draft.provider_draft_id = None
            db.session.commit()

        sig_html, sig_text = _org_signature(message)
        # De handtekening-kwargs alleen meegeven als er echt een handtekening is: zo
        # blijven providers (en test-fakes) zonder de argumenten gewoon werken.
        extra = {}
        if sig_html:
            # Interne /media-afbeeldingen (M365-foto, org-logo's) worden naar cid: herschreven
            # en als verborgen inline-bijlage meegestuurd; externe https-plaatjes blijven staan.
            try:
                from app.signatures import inline_images_for

                sig_html, inline_images = inline_images_for(sig_html)
            except Exception as exc:  # noqa: BLE001 — embedden mag een concept nooit tegenhouden
                current_app.logger.info("Inline-afbeeldingen overgeslagen: %s", exc)
                inline_images = []
            extra = {"signature_html": sig_html, "signature_text": sig_text}
            if inline_images:
                extra["inline_images"] = inline_images
        draft_id = provider.create_draft(
            thread_id=conversation,
            subject=f"RE: {message.subject}",
            body=draft.current_text,
            in_reply_to=message.provider_message_id,
            **extra,
        )
        # Meteen vastleggen: vanaf hier bestaat het concept in Outlook, en een fout
        # verderop mag het nooit meer onvindbaar maken.
        draft.provider_draft_id = draft_id
        draft.placed = bool(draft_id)
        db.session.commit()

        if draft_id and hasattr(provider, "mark_own_draft"):
            provider.mark_own_draft(draft_id)  # merkteken, zodat we het later herkennen
        return draft_id
    except (ProviderError, Exception) as exc:  # write-back mag de pijplijn nooit breken
        draft.placed = False  # zichtbaar maken dat het (nog) niet in Concepten staat
        current_app.logger.warning("Concept niet in Outlook geplaatst: %s", exc)
    return None

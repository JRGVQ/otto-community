"""Processing orchestrator (project document §5.4).

webhook/poll → sync → categorize → (if applicable) draft → write-back.
The chain ENDS at draft creation. There is no send step anywhere in the pipeline.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Message
from app.tasks.categorize import categorize_message
from app.tasks.draft import generate_draft


def process_incoming(message_id: int) -> None:
    message = db.session.get(Message, message_id)
    if not message:
        return

    # Idempotent: a duplicate delivery (webhook + poll, or a retry) must not re-run the AI
    # or write a second draft. Skip once the message is categorised AND its draft is settled
    # (a draft exists, or its category never wanted one). A categorised-but-undrafted message
    # still falls through, so a failed draft step can be retried.
    if message.category_id is not None:
        wants_draft = bool(message.category and message.category.draft_enabled
                           and not message.category.is_noise)
        if message.draft is not None or not wants_draft:
            return

    # 1. Categorise (skip if already done — see idempotency note above).
    #    Persoonlijke postvakken respecteren de schakelaar "Automatische categorisatie";
    #    gedeelde mailboxen hebben hun eigen instellingen. Uit betekent: de mail blijft
    #    ongesorteerd en (verderop) zonder concept. De inhaalslag gebeurt bij het weer
    #    aanzetten van de schakelaar (app/api/settings.py verwerkt dan de achterstand) —
    #    de sync zelf enqueuet elk bericht maar één keer.
    if message.category_id is None:
        owner = message.account.user if (message.account and not message.account.is_shared) else None
        if owner is not None and not owner.setting("categorisatie", default=True):
            return
        categorize_message(message_id)
        db.session.refresh(message)

    # 2. Draft — only if drafting is enabled for this box AND this category wants one.
    #    Shared mailbox → the mailbox's auto-draft toggle + its per-category override;
    #    personal → the user's setting + the org category's flag.
    acct = message.account
    category = message.category
    # Ruis vraagt geen mens: geen concept, en ook geen Teams-melding hieronder — ongeacht
    # wat de categorie verder zegt. Hier bespaart het ruisfilter zijn tweede AI-aanroep
    # (de eerste is de classificatie, die categorize_message al oversloeg).
    if category is not None and category.is_noise:
        return

    # Bevestigt deze mail een voorgesteld moment? Dan de afspraak klaarzetten (nooit boeken).
    # Best-effort en opt-in per postvak; een hapering mag de rest van de verwerking niet raken.
    try:
        from app.tasks.appointments import stage_from_message

        stage_from_message(message)
    except Exception:  # noqa: BLE001
        db.session.rollback()

    # Klantmail loggen in de eigen Odoo van de organisatie, als die gekoppeld is. Ook
    # best-effort en gescoped op de organisatie.
    try:
        from app.tasks.crm_log import log_customer_mail

        log_customer_mail(message)
    except Exception:  # noqa: BLE001
        db.session.rollback()
    if acct and acct.is_shared:
        from app.models.mailbox_category import effective_config

        auto = bool(acct.settings and acct.settings.auto_draft)
        cat_wants = effective_config(acct, category)[1] if category else False
        wants = auto and cat_wants
    else:
        user = acct.user if acct else None
        # default=True: een gebruiker zonder geseede rij (bv. via uitnodiging) mag
        # nooit stilletjes conceptgeneratie verliezen — zelfde regel als de andere gates.
        drafts_enabled = user.setting("drafts", default=True) if user else True
        wants = drafts_enabled and bool(category and category.draft_enabled)
    if category and wants:
        generate_draft(message_id)
    # Otto never sends — the chain ends at draft creation.

    # 3. Nudge the team's Microsoft Teams channel about actionable new mail.
    if acct and acct.is_shared and category and category.key == "te_beantwoorden":
        from app.tasks.teams_notify import notify_new_mail

        notify_new_mail(message)

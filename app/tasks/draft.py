"""Draft-generation task. Produces ONE concept reply in the user's preferred tone.

Only one draft is generated (not four) — that is the cheapest path and matches how
people work. The user can still switch tone afterwards; the alternative is generated
on demand (see app/api/drafts.set_tone). NEVER sends.
"""
from __future__ import annotations

from app.ai import get_service_for
from app.ai.metering import record_usage
from app.constants import DEFAULT_TONE, DRAFT_CONCEPT, DRAFT_SENT, TONES
from app.extensions import db
from app.models import Draft, DraftToneVariant, Message, Organization
from app.tasks.writeback import write_draft


class _MailboxStyle:
    """Adapts a shared mailbox's settings to the StyleProfile shape the prompt builder
    expects, so a team mailbox drafts in its OWN voice — never the connector's personal
    style. No personal few-shot examples leak in (raw_profile is empty)."""

    def __init__(self, settings):
        self.tone = settings.tone
        self.greeting = None
        self.signoff = None
        self.signature = settings.signature
        self.default_reply = settings.default_reply
        self.raw_profile: dict = {}


def signature_for(user, settings=None, account=None) -> str:
    """The sign-off for a draft. See app/signatures.py for the full order — a shared
    mailbox's own signature first, then admin-toewijzingen (gebruiker/groep), then the
    org's signature for the sending domain, then the user's own, then the org default.
    Never a placeholder."""
    from app.signatures import resolve

    return resolve(user, account=account, mailbox_settings=settings)


def prompt_signature_for(user, settings=None, account=None, message=None) -> str | None:
    """De handtekening zoals hij de AI-PROMPT in gaat: de platte tekst, of None wanneer
    een rijke (HTML) org-handtekening geldt — die wordt dan server-side onder het concept
    gezet bij de write-back (app/tasks/writeback.py), zodat het model hem niet dubbel
    (of verzonnen) kan schrijven.

    `message` voedt de regel-engine met dezelfde ontvanger-context als de write-back, zodat
    de prompt- en write-back-keuze niet uiteenlopen (geen dubbele handtekening)."""
    from app.signatures import resolve_rich

    resolved = resolve_rich(user, account=account, mailbox_settings=settings, message=message)
    return None if resolved.html else resolved.text


def preferred_tone_for(user, requested: str | None = None, settings=None) -> str:
    """An explicit request wins; then the mailbox's tone (shared mailboxes); then the
    user's saved preferred tone; else the default."""
    if requested in TONES:
        return requested
    if settings and settings.tone in TONES:
        return settings.tone
    style = user.style_profile if user else None
    if style and style.tone in TONES:
        return style.tone
    return DEFAULT_TONE


def _thread_already_answered(message: Message) -> bool:
    """True if we already replied somewhere in this conversation (matched on the raw
    provider conversation id — the thread_id FK is NULL for synced mail). Two signals:
    a sent Otto draft in the thread, OR a message flagged replied_at (set on send)."""
    if not message.provider_thread_id:
        return False
    replied = (
        db.session.query(Message.id)
        .filter(
            Message.organization_id == message.organization_id,
            Message.provider_thread_id == message.provider_thread_id,
            Message.replied_at.isnot(None),
        )
        .first()
    )
    if replied is not None:
        return True
    return (
        db.session.query(Draft.id)
        .join(Message, Draft.message_id == Message.id)
        .filter(
            Message.organization_id == message.organization_id,
            Message.provider_thread_id == message.provider_thread_id,
            Draft.status == DRAFT_SENT,
        )
        .first()
        is not None
    )


def _replied_in_provider(message: Message) -> bool:
    """True if the mailbox owner already sent a reply in this conversation, checked against
    the provider's Sent Items. Best-effort: a failed/unsupported check never blocks drafting."""
    account = message.account
    if not (account and message.provider_thread_id):
        return False
    try:
        from app.providers import get_provider

        provider = get_provider(account)
        if not provider.is_configured():
            return False
        return provider.has_sent_reply_after(message.provider_thread_id, message.received_at)
    except Exception:  # noqa: BLE001 — never block drafting on a check failure
        return False


def _thread_open_concept(message: Message) -> Draft | None:
    """An existing concept draft on ANOTHER message of the same conversation, if any.

    One reply per conversation → one concept per thread. Matched on the raw provider
    conversation id (the thread_id FK is NULL for synced mail), same as the already-replied
    guard. Returns the existing concept so the caller can no-op instead of stacking a
    second concept in the mailbox."""
    if not message.provider_thread_id:
        return None
    return (
        db.session.query(Draft)
        .join(Message, Draft.message_id == Message.id)
        .filter(
            Message.organization_id == message.organization_id,
            Message.provider_thread_id == message.provider_thread_id,
            Message.id != message.id,
            Draft.status == DRAFT_CONCEPT,
        )
        .order_by(Draft.id.desc())
        .first()
    )


def _sender_history(message: Message, max_pairs: int = 3, max_chars: int = 400) -> str | None:
    """Wat je eerder met deze afzender wisselde — in ANDERE gesprekken dan dit.

    `_thread_transcript` geeft de lopende conversatie; dit kijkt breder: waar ging het
    eerder over met deze persoon, en vooral — wat antwoordde jij toen? Dat laatste is de
    rijkste grond voor een nieuw concept: afspraken, tarieven, standaardantwoorden en toon
    zitten in je eigen eerdere replies.

    Strikt gescoped op hetzelfde postvak, zodat er nooit mail van een collega in jouw
    concept belandt. Puur lokaal: geen extra provider-oproep."""
    adres = (message.sender_email or "").strip().lower()
    if not adres:
        return None
    q = (
        db.session.query(Message)
        .filter(
            Message.organization_id == message.organization_id,
            db.func.lower(Message.sender_email) == adres,
            Message.id != message.id,
        )
    )
    # Zelfde postvak; zonder account (seed-mail) terugvallen op dezelfde eigenaar.
    q = q.filter(Message.account_id == message.account_id) if message.account_id \
        else q.filter(Message.user_id == message.user_id)
    if message.provider_thread_id:
        q = q.filter(db.or_(Message.provider_thread_id.is_(None),
                            Message.provider_thread_id != message.provider_thread_id))
    eerder = (
        q.order_by(db.func.coalesce(Message.received_at, Message.created_at).desc(), Message.id.desc())
        .limit(max_pairs)
        .all()
    )
    if not eerder:
        return None

    from app.ai.style import top_reply

    blokken = []
    for m in reversed(eerder):          # oudste eerst, zoals het transcript
        wanneer = m.received_at.strftime("%d-%m-%Y") if m.received_at else ""
        vraag = top_reply(m.body or m.snippet or "")[:max_chars].strip()
        regel = f"[{wanneer}] {m.subject or '(geen onderwerp)'}"
        if vraag:
            regel += f"\n  zij: {vraag}"
        # Jouw eigen antwoord van toen — het waardevolste deel.
        if m.draft and m.draft.status == DRAFT_SENT and m.draft.current_text:
            antwoord = top_reply(m.draft.current_text)[:max_chars].strip()
            if antwoord:
                regel += f"\n  jij: {antwoord}"
        blokken.append(regel)
    return "\n\n".join(blokken) or None


def _thread_transcript(message: Message, max_msgs: int = 6, max_chars: int = 600) -> str | None:
    """A compact transcript of the earlier messages in this conversation, so the draft
    fits the whole exchange. Oldest-first, current message excluded, trimmed for the prompt."""
    if not message.provider_thread_id:
        return None
    siblings = (
        db.session.query(Message)
        .filter(
            Message.organization_id == message.organization_id,
            Message.provider_thread_id == message.provider_thread_id,
            Message.id != message.id,
        )
        .order_by(db.func.coalesce(Message.received_at, Message.created_at).asc(), Message.id.asc())
        .all()
    )
    if not siblings:
        return None
    parts = []
    for m in siblings[-max_msgs:]:
        who = m.sender or m.sender_email or "?"
        when = m.received_at.strftime("%d-%m-%Y") if m.received_at else ""
        body = (m.body or m.snippet or "").strip().replace("\r", " ")
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        parts.append(f"[{when}] {who}: {body}")
    return "\n".join(parts) if parts else None


def generate_draft(message_id: int, tone: str | None = None, force: bool = False, intent: str | None = None) -> int | None:
    """Generate/refresh a single concept draft in the preferred tone. Returns the draft id.

    `intent` is an optional one-off steer ("what I want to reply") from the user; it shapes
    this generation but is deliberately NOT cached as a reusable tone variant.
    """
    message = db.session.get(Message, message_id)
    if not message:
        return None

    category = message.category
    if not force:
        acct0 = message.account
        if acct0 and acct0.is_shared:
            from app.models.mailbox_category import effective_config

            cat_draft = effective_config(acct0, category)[1] if category else False
        else:
            cat_draft = bool(category and category.draft_enabled)
        if not (category and cat_draft):
            return None

    # Already-replied guard: don't prepare a draft if Otto already sent a reply in this
    # conversation, OR if the owner already replied directly in Outlook. (Cheap DB check
    # first; the provider Sent-Items check only runs if that's clear.) Force overrides.
    if not force and (_thread_already_answered(message) or _replied_in_provider(message)):
        return None

    # One concept per conversation: if another message in this thread already has a concept
    # draft, don't stack a second one in the mailbox — keep the existing single reply.
    if not force:
        existing_concept = _thread_open_concept(message)
        if existing_concept is not None:
            return existing_concept.id

    org = db.session.get(Organization, message.organization_id)
    service = get_service_for(org)
    acct = message.account
    user = acct.user if acct else None
    # Shared mailbox → draft in the MAILBOX's voice (tone/signature/default reply),
    # ignoring the connecting member's personal style. Personal mailbox → own style.
    mb = acct.settings if (acct and acct.is_shared) else None
    if mb:
        style = _MailboxStyle(mb)
        signature = prompt_signature_for(user, mb, account=acct, message=message)
        chosen = preferred_tone_for(user, tone, mb)
    else:
        style = user.style_profile if user else None
        signature = prompt_signature_for(user, account=acct, message=message)
        chosen = preferred_tone_for(user, tone)

    thread = _thread_transcript(message)
    history = _sender_history(message)
    text = service.draft(message.sender, message.subject, message.body, chosen, style, message.lang, signature, org=org, intent=intent, thread=thread, history=history, account=acct)
    record_usage(message.organization_id, service.model_name, *service.pop_usage())

    draft = message.draft
    if draft is None:
        draft = Draft(message_id=message.id, lang=message.lang)
        db.session.add(draft)
        db.session.flush()

    # One draft, in the chosen tone. Extra tones are produced on demand later.
    # An intent-steered draft is one-off, so we never persist it as a tone variant
    # (which would otherwise be reused for a later plain regenerate/tone-switch).
    for v in list(draft.tone_variants):
        db.session.delete(v)
    if not (intent and intent.strip()):
        db.session.add(DraftToneVariant(draft_id=draft.id, tone=chosen, text=text))

    draft.status = "concept"
    draft.current_tone = chosen
    draft.current_text = text
    db.session.flush()

    # Write-back naar de Concepten-map van de provider — tenzij de gebruiker
    # "Concepten klaarzetten in mailbox" uitzette (persoonlijke postvakken; gedeelde
    # mailboxen volgen hun eigen instellingen en schrijven altijd terug). Uit? Dan ook
    # een eerder geplaatst concept opruimen, anders blijft daar een verouderde tekst
    # staan die de Verzenden-knop nog zou versturen.
    acct = message.account
    owner = acct.user if (acct and not acct.is_shared) else None
    if owner is None or owner.setting("klaarzetten", default=True):
        write_draft(draft)  # write-back to provider Drafts folder when configured
    else:
        from app.tasks.writeback import remove_placed_draft

        remove_placed_draft(draft)  # bewust alleen in Otto — niet "mislukt"
    db.session.commit()
    return draft.id

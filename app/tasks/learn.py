"""Learn a user's writing style from their own mail communication.

Two paths:

* `deep=False` (default, gedraaid bij élke verzending): goedkoop en heuristisch, uit de
  signalen die we toch al hebben — de concepten die de gebruiker verstuurde (hun goedgekeurde
  woordkeuze) en hun bewerkingen op Otto's concepten. Geen provider-oproep, geen LLM-kost.

* `deep=True` (de knop "Leer uit mijn verzonden mail"): leest de échte Verzonden-items uit
  het postvak en laat het AI-model beschrijven hóé de gebruiker antwoordt — een leesbare
  stijlbeschrijving plus begroeting/afsluiting/toon/voorbeelden. Zo doet de knop wat ze
  belooft, ook voor wie nog nooit een Otto-concept verstuurde.
"""
from __future__ import annotations

from flask import current_app

from app.constants import DRAFT_SENT
from app.extensions import db
from app.models.base import utcnow

SENT_LOOKBACK_DAYS = 120   # ruim genoeg voor een representatief staal van iemands stijl
SENT_CAP = 40              # per postvak — genoeg om de stijl te vatten, geen marathon


def _internal_samples(user_id: int) -> list[str]:
    """Wat we zonder de mailbox al weten: verstuurde Otto-concepten + bewerkingen erop."""
    from app.models import Draft, Feedback, Message

    sent = [
        d.current_text
        for d in (
            db.session.query(Draft)
            .join(Message, Draft.message_id == Message.id)
            .filter(Message.user_id == user_id, Draft.status == DRAFT_SENT)
            .all()
        )
        if d.current_text
    ]
    edits = [
        f.to_value
        for f in db.session.query(Feedback).filter_by(user_id=user_id, kind="draft_edit").all()
        if f.to_value
    ]
    return sent + edits


def _sent_mail_samples(user) -> list[str]:
    """De volledige tekst van recent verzonden mail uit de eigen (niet-gedeelde) postvakken.

    Best-effort: een postvak dat niet (meer) verbonden is of een provider-hapering slaan we
    stil over — de knop mag hier nooit op vastlopen."""
    from app.providers import get_provider

    out: list[str] = []
    for acct in getattr(user, "mail_accounts", []) or []:
        if acct.status != "connected" or acct.is_shared:
            continue  # persoonlijke stijl = eigen postvak, niet een gedeeld teampostvak
        try:
            provider = get_provider(acct)
            if not (provider.is_configured() and hasattr(provider, "list_recent_sent")):
                continue
            for m in provider.list_recent_sent(days=SENT_LOOKBACK_DAYS, limit=SENT_CAP):
                body = (m.get("body") or "").strip()
                if body:
                    out.append(body)
        except Exception:  # noqa: BLE001 — een provider-hapering mag het leren niet stoppen
            current_app.logger.info("[stijl] verzonden mail ophalen mislukt voor postvak %s", acct.id)
    return out


def learn_user_style(user_id: int, *, deep: bool = False):
    """Refresh the user's StyleProfile. Returns it (or None when there's nothing to learn).

    `deep=True` leest de échte Verzonden-items en gebruikt het AI-model voor een leesbare
    stijlbeschrijving; `deep=False` blijft de goedkope heuristiek voor het verzend-pad."""
    from app.ai.style import learn_from_sent
    from app.models import StyleProfile, User

    user = db.session.get(User, user_id)
    if not user:
        return None

    texts = _internal_samples(user_id)
    if deep:
        # Echte verzonden mail vooraan: dat is de rijkste, meest representatieve bron.
        texts = _sent_mail_samples(user) + texts
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return None

    description = None
    if deep:
        from app.ai import get_service_for
        from app.ai.metering import record_usage

        org = getattr(user, "organization", None)
        try:
            service = get_service_for(org)
            learned = service.analyze_style(texts)
            if org is not None:
                record_usage(org.id, service.model_name, *service.pop_usage())
        except Exception:  # noqa: BLE001 — val terug op de heuristiek, nooit een 500
            current_app.logger.exception("[stijl] AI-analyse mislukt; heuristiek gebruikt")
            learned = learn_from_sent(texts)
        description = (learned.get("description") or "").strip() or None
    else:
        learned = learn_from_sent(texts)

    sp = user.style_profile
    if sp is None:
        sp = StyleProfile(user_id=user_id)
        db.session.add(sp)
    raw = dict(sp.raw_profile or {})
    # Fields the user edited by hand are pinned: learning refines the profile, it never
    # overrules a person who told us what they want. Without this, an "editable profile"
    # would silently revert on the next sent mail.
    pinned = set(raw.get("pinned") or [])
    if learned.get("greeting") and "greeting" not in pinned:
        sp.greeting = learned["greeting"]
    if learned.get("signoff") and "signoff" not in pinned:
        sp.signoff = learned["signoff"]
    if "examples" not in pinned:
        raw["examples"] = learned.get("examples", [])
    # De stijlbeschrijving wordt automatisch opgesteld — verversen wanneer we er een hebben
    # (alleen in het diepe pad), maar een bestaande nooit met leeg overschrijven.
    if description:
        raw["description"] = description
    sp.raw_profile = raw
    sp.learned_at = utcnow()
    db.session.commit()
    return sp


def pin_style_fields(style_profile, fields) -> None:
    """Mark fields as user-set, so learn_user_style leaves them alone from now on."""
    raw = dict(style_profile.raw_profile or {})
    raw["pinned"] = sorted(set(raw.get("pinned") or []) | set(fields))
    style_profile.raw_profile = raw


def unpin_style_fields(style_profile, fields) -> None:
    """Hand fields back to the learner."""
    raw = dict(style_profile.raw_profile or {})
    raw["pinned"] = sorted(set(raw.get("pinned") or []) - set(fields))
    style_profile.raw_profile = raw


# ── Periodieke verversing ────────────────────────────────────────────────────
STYLE_REFRESH_DAYS = 7    # hoe oud een stijlprofiel mag worden
STYLE_REFRESH_CAP = 20    # gebruikers per ronde — spreidt de AI-kost over de week


def refresh_style_profiles(limit: int = STYLE_REFRESH_CAP) -> int:
    """Ververs verouderde stijlprofielen uit de ECHTE verzonden mail.

    Zonder deze taak gebeurde dat alleen wanneer iemand zelf op "Opnieuw leren" drukte:
    `deep=True` stond nergens anders. Je schrijfstijl veranderde dus nooit mee met wat je
    werkelijk verstuurt — precies de klacht. Nu loopt het wekelijks vanzelf.

    Bewust begrensd: alleen gebruikers met een verbonden eigen postvak, hoogstens `limit`
    per ronde, en alleen als het profiel ouder is dan STYLE_REFRESH_DAYS. Eén AI-call per
    gebruiker per week. Vastgezette velden blijven onaangeroerd (zie learn_user_style)."""
    from datetime import timedelta

    from app.models import MailAccount, StyleProfile, User

    grens = utcnow() - timedelta(days=STYLE_REFRESH_DAYS)
    kandidaten = (
        db.session.query(User.id)
        .join(MailAccount, MailAccount.user_id == User.id)
        .outerjoin(StyleProfile, StyleProfile.user_id == User.id)
        .filter(
            MailAccount.status == "connected",
            MailAccount.mailbox_type != "shared",
            db.or_(StyleProfile.id.is_(None), StyleProfile.learned_at.is_(None),
                   StyleProfile.learned_at < grens),
        )
        .distinct()
        .limit(max(1, int(limit)))
        .all()
    )
    ververst = 0
    for (uid,) in kandidaten:
        try:
            if learn_user_style(uid, deep=True) is not None:
                ververst += 1
        except Exception:  # noqa: BLE001 — één gebruiker mag de ronde niet stoppen
            db.session.rollback()
            current_app.logger.warning("[stijl] verversen mislukt voor gebruiker %s", uid)
    if ververst:
        current_app.logger.info("[stijl] %s stijlprofiel(en) ververst uit verzonden mail", ververst)
    return ververst

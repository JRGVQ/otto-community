"""Ruis herkennen vóór de AI eraan te pas komt.

Een nieuwsbrief kost vandaag evenveel als een klantvraag: elke binnenkomende mail gaat
door de LLM-classificatie, en soms ook nog door het schrijven van een concept dat
niemand ooit verstuurt. Voor een postvak dat half uit mailings bestaat is dat het
grootste deel van het AI-budget, besteed aan mail die de gebruiker toch niet leest.

Het signaal hoeft niet van een model te komen. Bulkmail draagt een `List-Unsubscribe`-
header (RFC 2369/8058) — Otto haalt die er al uit bij het synchroniseren om de
uitschrijfknop te kunnen tonen. Een mail met zo'n header is per definitie verstuurd
naar een lijst, niet aan één persoon. Dat is een hardere aanwijzing dan wat een
classificatie ervan zou maken, en hij is gratis.

Bewust conservatief: liever een nieuwsbrief die alsnog door de AI gaat dan een
klantmail die als ruis verdwijnt. Daarom staat het filter standaard uit, geldt het
alleen als de organisatie zelf een categorie als ruis heeft aangeduid, en telt een
antwoord in een lopend gesprek nooit als ruis — een echte conversatie krijgt geen
`List-Unsubscribe`, maar een geautomatiseerde bevestiging in dezelfde thread soms wel.
"""
from __future__ import annotations

import re

# Afzenders die per definitie geen antwoord verwachten. De vorm `no-reply@`,
# `noreply@`, `donotreply@`, `no_reply@` en varianten met een punt ertussen.
_NOREPLY = re.compile(r"^(no[._-]?reply|do[._-]?not[._-]?reply|bounce|mailer[._-]?daemon)@", re.I)


def looks_like_bulk(message) -> bool:
    """Is dit bulkmail? Alleen harde, gratis signalen — nooit een gok over de inhoud."""
    if message is None:
        return False
    # RFC 2369/8058: de afzender biedt een uitschrijfmogelijkheid, dus dit ging naar een lijst.
    if getattr(message, "unsubscribe_url", None) or getattr(message, "unsubscribe_mailto", None):
        return True
    return bool(_NOREPLY.match((getattr(message, "sender_email", None) or "").strip()))


def noise_category_for(org_id: int):
    """De categorie die deze organisatie als ruis heeft aangeduid, of None.

    Meerdere ruiscategorieën mogen bestaan (nieuwsbrieven, meldingen, …); zonder verdere
    aanwijzing gaat ongevraagde bulk naar de eerste in beheervolgorde."""
    from app.extensions import db
    from app.models import Category

    return (
        db.session.query(Category)
        .filter_by(organization_id=org_id, is_noise=True)
        .order_by(Category.position, Category.id)
        .first()
    )


def filter_enabled_for(message) -> bool:
    """Heeft dit postvak het ruisfilter aanstaan? Opt-in, net als automatisch mappen:
    een gedeeld postvak volgt zijn eigen instelling, een persoonlijk dat van de eigenaar."""
    acct = getattr(message, "account", None)
    if acct is None:
        return False
    if acct.is_shared:
        return bool(getattr(acct.settings, "noise_filter_enabled", False))
    owner = acct.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "noise_filter_enabled", False))


def classify_as_noise(message):
    """De ruiscategorie voor dit bericht, of None als het gewoon door de AI moet.

    Geeft None terug zodra er ook maar iets niet klopt — het filter mag nooit de reden
    zijn dat een mail niet behandeld wordt."""
    if not filter_enabled_for(message) or not looks_like_bulk(message):
        return None
    # Een antwoord in een gesprek waar al iets mee gebeurd is, is geen ruis meer.
    if getattr(message, "replied_at", None) or getattr(message, "assignee_user_id", None):
        return None
    return noise_category_for(message.organization_id)

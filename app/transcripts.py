"""Actiepunten uit een vergadertranscriptie halen.

Geen notulen-bot. Otto neemt niets op en vraagt geen toestemming per deelnemer — dat botst
met de Belgische praktijk en met Otto's positionering. Dit werkt op transcripties die de
tenant zélf al laat maken (Teams maakt ze op verzoek); Otto leest ze alleen en haalt er de
actiepunten uit, zodat ze op de takenlijst van het team belanden in plaats van te
verdampen zodra de vergadering voorbij is.

Bewust zonder AI. Een transcript door een model halen is duur en het resultaat is niet uit
te leggen ("waarom is dít een actiepunt?"). Hier is het antwoord altijd zichtbaar: er staat
een expliciet actiepunt-label, óf iemand zegt in de eerste persoon dat hij iets gaat doen,
óf er staat "X doet Y". En bewust streng, net als bij de toezeggingen: een gemist actiepunt
kost niets, een verzonnen actiepunt kost vertrouwen in de lijst.

Alles hier is tekstwerk: geen Graph, geen database. Het ophalen van de transcriptie zit in
de provider, het wegschrijven naar taken in `app/tasks/transcripts.py`.
"""
from __future__ import annotations

import re
from datetime import date

from app.commitments import _kort, parse_due

# Een expliciet actiepunt-label. Het duidelijkste signaal — dit staat er nooit per ongeluk.
_LABEL = re.compile(r"^\s*(?:actiepunt|actie|action\s*item|action|to-?do|taak)\s*[:\-]\s*(?P<rest>.+)$",
                    re.IGNORECASE)

# Een naam gevolgd door een werkwoord dat op een toezegging wijst — "Tom stuurt de offerte",
# "Marie belt de leverancier". Bewust met een beperkte werkwoordenlijst, zodat een gewone
# zin ("Tom vindt dat...") geen actiepunt wordt.
_TOEWIJZING = re.compile(
    r"\b(?P<naam>[A-Z][a-zà-ÿ]+)\s+"
    r"(?P<ww>stuurt|bezorgt|belt|maakt|regelt|plant|bekijkt|checkt|volgt\s+op|neemt\s+op|"
    r"zorgt\s+voor|contacteert|mailt|verstuurt|levert|bereidt\s+voor)\b",
    re.IGNORECASE,
)

# Eerste persoon: de spreker zelf zegt iets te gaan doen. De eigenaar is dan de spreker.
_EERSTE_PERSOON = re.compile(
    r"\bik\s+(stuur|bezorg|bel|maak|regel|plan|bekijk|check|volg|neem|zorg|contacteer|"
    r"mail|verstuur|lever|bereid|kom\s+erop\s+terug)\b",
    re.IGNORECASE,
)

# Een parenthese met een naam achter een actiepunt: "website nakijken (Tom)".
_PAREN_NAAM = re.compile(r"\(([A-Z][a-zà-ÿ]+)\)\s*$")

# Kopregels van een transcriptie: "Jan Peeters   0:03" of "Jan Peeters:". Hieraan hangen we
# de eerste-persoon-actiepunten op, zodat "ik bel maandag" bij de juiste naam komt.
_SPREKER = re.compile(r"^\s*(?P<naam>[A-Za-zà-ÿ][\w.'\- ]{0,48}?)\s*(?:\t|\s{2,})?"
                      r"(?:\d{1,2}:\d{2}(?::\d{2})?)\s*$")
_SPREKER_DP = re.compile(r"^\s*(?P<naam>[A-Z][\w.'\- ]{0,48}?):\s*(?P<rest>.*)$")

MAX_ITEMS = 20   # meer dan dit uit één vergadering is bijna altijd ruis


def _speaker_segments(text: str):
    """(spreker, zin) per zin. Kopregels ("Naam 0:03") zetten de huidige spreker; een regel
    "Naam: tekst" geldt voor die ene regel."""
    spreker = None
    for regel in (text or "").splitlines():
        regel = regel.rstrip()
        if not regel.strip():
            continue
        # Een expliciet actiepunt-label gaat vóór de "Naam: tekst"-regel — anders leest
        # "Actiepunt: ..." als een spreker "Actiepunt" en verdwijnt het label.
        if _LABEL.match(regel):
            yield spreker, regel.strip()
            continue
        if m := _SPREKER.match(regel):
            spreker = m.group("naam").strip()
            continue
        if m := _SPREKER_DP.match(regel):
            rest = m.group("rest").strip()
            naam = m.group("naam").strip()
            if rest:
                for zin in _split(rest):
                    yield naam, zin
            else:
                spreker = naam
            continue
        for zin in _split(regel):
            yield spreker, zin


def _split(regel: str):
    for zin in re.split(r"(?<=[.!?])\s+", regel.strip()):
        zin = zin.strip()
        if zin:
            yield zin


def _owner_from(zin: str, spreker: str | None) -> str | None:
    if m := _PAREN_NAAM.search(zin):
        return m.group(1)
    if m := _TOEWIJZING.search(zin):
        return m.group("naam")
    if _EERSTE_PERSOON.search(zin):
        return spreker
    return None


def extract_action_items(text: str | None, meeting_date: date) -> list[dict]:
    """De actiepunten in deze transcriptie, als [{"text", "owner", "due"}].

    `owner` is een naam of None; `due` een datum of None. Streng: er moet een expliciet
    label staan, óf een eerste-persoon-toezegging, óf een naam-plus-werkwoord."""
    gevonden: list[dict] = []
    gezien: set[str] = set()
    for spreker, zin in _speaker_segments(text or ""):
        owner = None
        kern = zin
        if m := _LABEL.match(zin):
            kern = m.group("rest").strip()
            owner = _owner_from(kern, spreker) or spreker
        elif _EERSTE_PERSOON.search(zin):
            owner = spreker
        elif _TOEWIJZING.search(zin):
            owner = _TOEWIJZING.search(zin).group("naam")
        else:
            continue

        sleutel = re.sub(r"\s+", " ", kern.lower()).strip()
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        due = parse_due(zin, meeting_date)
        gevonden.append({"text": _kort(kern), "owner": owner, "due": due})
        if len(gevonden) >= MAX_ITEMS:
            break
    return gevonden


# ── De brug naar een postvak ─────────────────────────────────────────────────
def enabled_for(account) -> bool:
    """Opt-in per postvak, net als de andere agenda-/vergaderfuncties."""
    if account is None:
        return False
    if account.is_shared:
        return bool(getattr(account.settings, "transcripts_enabled", False))
    owner = account.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "transcripts_enabled", False))

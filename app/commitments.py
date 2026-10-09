"""Toezeggingen in je eigen verzonden mail herkennen.

"Ik bezorg je dat morgen" is een afspraak die je maakt terwijl je aan het typen bent, en
precies daarom vergeet je hem: er komt geen mail van, er staat niets in je agenda, en de
enige plek waar hij bestaat is een mailtje dat je zelf verstuurd hebt. Otto leest je
verzonden mail al voor de opvolgsweep; daar zit dit gratis bij.

Bewust zonder AI. Elke verzonden mail door een model halen kost tokens voor iets dat een
handvol zinsbouwen is, en het resultaat zou niet uit te leggen zijn ("waarom is dít een
taak geworden?"). Hier is het antwoord altijd: er staat een belofte én een termijn in
dezelfde zin.

En bewust streng. Een gemiste toezegging kost je niets — je had toch al niets. Een
verzonnen taak kost vertrouwen in de takenlijst, en een takenlijst die je niet vertrouwt
gebruik je niet meer. Vandaar dat belofte én termijn samen in één zin moeten staan.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# Wat een belofte is: iets dat *ik* (of wij) gaan doen. "Kan je dat morgen sturen?" is
# geen toezegging van mij, dus de eerste persoon is een voorwaarde.
_PROMISE = re.compile(
    r"\b("
    r"ik\s+(stuur|bezorg|mail|bel|laat|maak|zorg|bekijk|check|kom|volg|geef|breng|regel|plan)"
    r"|we\s+(sturen|bezorgen|mailen|bellen|laten|maken|zorgen|bekijken|komen|volgen|geven|regelen|plannen)"
    r"|ik\s+kom\s+erop\s+terug"
    r"|je\s+(hoort|krijgt|ontvangt)\s+(het|dit|dat|van\s+(mij|ons))"
    r"|i\s?('|’)?ll\s+(send|get|call|check|let|come|follow|share)"
    r"|i\s+will\s+(send|get|call|check|let|come|follow|share)"
    r")\b",
    re.IGNORECASE,
)

_DAGEN = {
    "maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3,
    "vrijdag": 4, "zaterdag": 5, "zondag": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Termijnen, van specifiek naar algemeen — de eerste die past wint, zodat "volgende week
# maandag" niet als los "maandag" of los "volgende week" gelezen wordt.
_WHEN = (
    ("overmorgen", lambda d: d + timedelta(days=2)),
    ("vandaag", lambda d: d),
    ("morgen", lambda d: d + timedelta(days=1)),
    ("tomorrow", lambda d: d + timedelta(days=1)),
    ("today", lambda d: d),
)

_BINNEN = re.compile(r"\bbinnen\s+(\d{1,2})\s*(dag|dagen|werkdag|werkdagen|week|weken)\b", re.I)
_VOLGENDE_WEEK_DAG = re.compile(
    r"\bvolgende\s+week\s+(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b", re.I)
_VOLGENDE_WEEK = re.compile(r"\b(volgende\s+week|next\s+week)\b", re.I)
_EINDE_WEEK = re.compile(r"\b(eind(e)?\s+(van\s+)?(de\s+)?week|deze\s+week|end\s+of\s+(the\s+)?week)\b", re.I)
_LOSSE_DAG = re.compile(
    r"\b(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)

# Hoe lang een toezegging vooruit mag liggen. Verder dan een maand is het geen "ik doe dit
# even" meer, en dan is de kans op een verkeerd gelezen zin groter dan de winst.
MAX_DAYS_AHEAD = 31
MAX_TASKS_PER_MAIL = 2   # meer dan dit uit één mail is bijna altijd een leesfout


def _plus_werkdagen(vanaf: date, aantal: int) -> date:
    """`aantal` werkdagen verder — weekends tellen niet mee. Wie "binnen 3 werkdagen"
    schrijft op een donderdag, bedoelt dinsdag, niet zondag."""
    d = vanaf
    for _ in range(max(0, aantal)):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def _next_weekday(vanaf: date, weekday: int) -> date:
    """De eerstvolgende <weekday> ná `vanaf`. "Ik bel je vrijdag" op een vrijdag betekent
    volgende vrijdag — anders zou de taak vandaag al te laat zijn."""
    vooruit = (weekday - vanaf.weekday()) % 7
    return vanaf + timedelta(days=vooruit or 7)


def parse_due(zin: str, sent_on: date) -> date | None:
    """De datum die deze zin noemt, of None. Relatief aan wanneer de mail verstuurd is —
    "morgen" in een mail van vorige week is niet morgen."""
    if m := _VOLGENDE_WEEK_DAG.search(zin):
        maandag = sent_on + timedelta(days=7 - sent_on.weekday())
        return maandag + timedelta(days=_DAGEN[m.group(1).lower()])
    if m := _BINNEN.search(zin):
        aantal, eenheid = int(m.group(1)), m.group(2).lower()
        # "werkdagen" begint óók met "we" — daarom expliciet, niet op een prefix.
        if eenheid in ("week", "weken"):
            return sent_on + timedelta(days=aantal * 7)
        if eenheid in ("werkdag", "werkdagen"):
            return _plus_werkdagen(sent_on, aantal)
        return sent_on + timedelta(days=aantal)
    for woord, bereken in _WHEN:
        if re.search(rf"\b{woord}\b", zin, re.IGNORECASE):
            return bereken(sent_on)
    if _VOLGENDE_WEEK.search(zin):
        return sent_on + timedelta(days=7 - sent_on.weekday())      # maandag volgende week
    if _EINDE_WEEK.search(zin):
        vrijdag = sent_on + timedelta(days=(4 - sent_on.weekday()) % 7)
        return vrijdag if vrijdag >= sent_on else _next_weekday(sent_on, 4)
    if m := _LOSSE_DAG.search(zin):
        return _next_weekday(sent_on, _DAGEN[m.group(1).lower()])
    return None


def _zinnen(tekst: str) -> list[str]:
    """Grof op zinnen splitsen. Hoeft niet perfect: het gaat erom dat belofte en termijn
    bij elkaar staan, niet toevallig drie alinea's uit elkaar."""
    schoon = re.sub(r"\s+", " ", tekst or "")
    return [z.strip() for z in re.split(r"(?<=[.!?;\n])\s+|\n", schoon) if z.strip()]


def _kort(zin: str, limiet: int = 160) -> str:
    return zin if len(zin) <= limiet else zin[: limiet - 1].rstrip() + "…"


def find(body: str | None, sent_on: date) -> list[dict]:
    """De toezeggingen in deze verzonden mail, als [{"text", "due"}].

    Belofte en termijn moeten in dezelfde zin staan. Dat is streng, en dat hoort ook: een
    verzonnen taak is duurder dan een gemiste."""
    gevonden: list[dict] = []
    gezien: set[date] = set()
    for zin in _zinnen(body or ""):
        if not _PROMISE.search(zin):
            continue
        due = parse_due(zin, sent_on)
        if due is None or due < sent_on:
            continue
        if (due - sent_on).days > MAX_DAYS_AHEAD:
            continue
        if due in gezien:      # twee zinnen over dezelfde dag is één afspraak
            continue
        gezien.add(due)
        gevonden.append({"text": _kort(zin), "due": due})
        if len(gevonden) >= MAX_TASKS_PER_MAIL:
            break
    return gevonden


# ── Instellingen per postvak ─────────────────────────────────────────────────
def enabled_for(account) -> bool:
    """Opt-in, net als de opvolgingen en het ruisfilter."""
    if account is None:
        return False
    if account.is_shared:
        return bool(getattr(account.settings, "commitments_enabled", False))
    owner = account.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "commitments_enabled", False))

"""Een bevestigd moment uit een binnenkomend antwoord herkennen — en klaarzetten.

Otto stelt in een concept vrije momenten voor (zie `app/scheduling.py`). Zegt de klant
daarna "dinsdag om 14u past", dan is er een afspraak — maar die staat nergens: niet in de
agenda, niet op een lijst. Dit herkent zo'n bevestiging en zet de afspraak *klaar*.

Bewust klaarzetten, niet boeken. Otto handelt nooit zelf — dat is het sterkste
verkoopargument. Een herkende bevestiging wordt een concept-afspraak die de gebruiker met
één klik in de agenda zet (of weggooit). Zo blijft de agenda van de gebruiker, en een
verkeerd gelezen zin kost hoogstens één klik, geen dubbele boeking.

Bewust zonder AI en bewust streng, net als de toezeggingen (6.5a): er moet én een
instemming ("past", "prima", "akkoord") én één concreet moment (dag + tijdstip) in de mail
staan. Een gemiste bevestiging kost niets — de gebruiker ziet de mail toch. Een verzonnen
afspraak kost vertrouwen, en een agenda die je niet vertrouwt gebruik je niet.

Alles hier is tekst- en tijdrekenwerk: geen Graph, geen database. Het lezen van de mailbox
zit in de provider, het klaarzetten in `app/tasks/appointments.py`.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

# Instemming: de klant gáát akkoord. Breed in drie talen, want dit filter bepaalt samen
# met "is er een concreet moment" of we iets klaarzetten — en klaarzetten is laagdrempelig
# (de gebruiker bevestigt zelf), dus een keer te veel is goedkoper dan een keer te weinig.
_ACCEPTS = re.compile(
    r"\b("
    r"past(\s+(prima|goed|me))?|schikt|komt?\s+goed\s+uit|is\s+goed|prima|akkoord|"
    r"in\s+orde|geen\s+probleem|lukt|graag|perfect|top|oké|oke|oké|okay|ok|"
    r"doen\s+we|dan\s+doen\s+we|zie\s+ik\s+je|tot\s+dan|bevestig|"
    r"dat\s+werkt|werkt\s+(voor\s+(mij|ons)|prima|goed)|"
    r"works\s+for\s+me|sounds\s+good|that\s+works|confirmed?|let'?s\s+(do|meet)|see\s+you|"
    r"d'accord|ça\s+marche|ca\s+marche|parfait|convient|c'est\s+bon"
    r")\b",
    re.IGNORECASE,
)

_DAGEN = {
    "maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3,
    "vrijdag": 4, "zaterdag": 5, "zondag": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}
_MAANDEN = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7,
    "october": 10,
}

# Tijdstip: "14u", "14u30", "14:00", "14.30", "14 uur", "14h", "14h30", "2pm", "2:30 pm".
# De minuten zijn optioneel; "half drie" e.d. laten we bewust liggen (te foutgevoelig).
_TIJD = re.compile(
    r"\b(?:om|rond|around|at|vers|à)?\s*"
    r"(?P<h>[0-2]?\d)"
    r"(?:[:. u h](?P<m>[0-5]\d))?"
    r"\s*(?P<uur>u(?:ur)?|h|(?P<ap>am|pm))?\b",
    re.IGNORECASE,
)

_DATUM_CIJFERS = re.compile(r"\b(?P<d>[0-3]?\d)[/\-.](?P<m>[01]?\d)(?:[/\-.](?P<y>\d{2,4}))?\b")
_DATUM_WOORD = re.compile(
    r"\b(?P<d>[0-3]?\d)\s+(?P<maand>[a-zéû]+)\b", re.IGNORECASE)
_VOLGENDE_WEEK_DAG = re.compile(
    r"\bvolgende\s+week\s+(?P<dag>[a-z]+)\b", re.IGNORECASE)

# Niet vandaag over een kwartier klaarzetten en niet verder dan een maand vooruit: buiten
# dat venster is de kans op een verkeerd gelezen zin groter dan de winst.
MIN_LEAD_MIN = 30
MAX_DAYS_AHEAD = 31
DEFAULT_DURATION_MIN = 30


def _find_time(tekst: str) -> time | None:
    """Het eerste plausibele tijdstip in de tekst. Vereist een uur-aanwijzing (u/h/uur/am/pm
    of dubbele punt) zodat "de 3 offertes" nooit 03:00 wordt."""
    for m in _TIJD.finditer(tekst):
        heeft_minuut = m.group("m") is not None
        marker = (m.group("uur") or "").lower()
        heeft_uurwoord = bool(marker)
        # Zonder minuten én zonder uur-woord is een los getal geen tijdstip.
        if not (heeft_minuut or heeft_uurwoord):
            continue
        uur = int(m.group("h"))
        minuut = int(m.group("m")) if heeft_minuut else 0
        if marker == "pm" and uur < 12:
            uur += 12
        elif marker == "am" and uur == 12:
            uur = 0
        if 0 <= uur <= 23 and 0 <= minuut <= 59:
            return time(uur, minuut)
    return None


def _explicit_date(tekst: str, vanaf: date) -> date | None:
    """Een expliciete datum ("26 augustus", "26/08"), of None. Ligt de datum al achter ons
    voor dit jaar, dan bedoelt men volgend jaar."""
    if m := _DATUM_WOORD.search(tekst):
        maand = _MAANDEN.get(m.group("maand").lower())
        if maand:
            try:
                d = date(vanaf.year, maand, int(m.group("d")))
            except ValueError:
                return None
            return d if d >= vanaf else date(vanaf.year + 1, maand, int(m.group("d")))
    if m := _DATUM_CIJFERS.search(tekst):
        dag, maand = int(m.group("d")), int(m.group("m"))
        jaar = m.group("y")
        try:
            if jaar:
                jr = int(jaar) + (2000 if len(jaar) == 2 else 0)
                return date(jr, maand, dag)
            d = date(vanaf.year, maand, dag)
            return d if d >= vanaf else date(vanaf.year + 1, maand, dag)
        except ValueError:
            return None
    return None


def _relative_date(tekst: str, vanaf: date) -> date | None:
    """Een dag-aanduiding relatief aan wanneer de mail binnenkwam: vandaag/morgen,
    "volgende week dinsdag", of een losse weekdag."""
    laag = tekst.lower()
    if re.search(r"\bovermorgen\b", laag):
        return vanaf + timedelta(days=2)
    if re.search(r"\b(morgen|tomorrow|demain)\b", laag):
        return vanaf + timedelta(days=1)
    if re.search(r"\b(vandaag|today|aujourd)\b", laag):
        return vanaf
    if m := _VOLGENDE_WEEK_DAG.search(laag):
        wd = _DAGEN.get(m.group("dag"))
        if wd is not None:
            maandag = vanaf + timedelta(days=7 - vanaf.weekday())
            return maandag + timedelta(days=wd)
    for woord, wd in _DAGEN.items():
        if re.search(rf"\b{woord}\b", laag):
            vooruit = (wd - vanaf.weekday()) % 7
            return vanaf + timedelta(days=vooruit)   # vandaag telt mee; tijd beslist verderop
    return None


def parse_moment(body: str | None, received_at: datetime, tz) -> datetime | None:
    """Het concrete moment dat deze mail noemt, tijdzone-bewust, of None.

    Vereist een tijdstip. Een dag mag ontbreken (dan: dezelfde dag), maar het resultaat moet
    in de toekomst liggen en binnen een maand — anders is het te vaag of te riskant."""
    tekst = re.sub(r"\s+", " ", body or "")
    if not tekst:
        return None
    klok = _find_time(tekst)
    if klok is None:
        return None
    dag = _explicit_date(tekst, received_at.date())
    if dag is None:
        dag = _relative_date(tekst, received_at.date())
    bare_time = dag is None
    if dag is None:
        dag = received_at.date()

    moment = datetime.combine(dag, klok, tzinfo=tz)
    ontvangen = received_at.astimezone(tz) if received_at.tzinfo else received_at.replace(tzinfo=tz)

    if moment <= ontvangen + timedelta(minutes=MIN_LEAD_MIN):
        # Een losse weekdag die vandaag al voorbij is → volgende week. Een kaal tijdstip dat
        # al voorbij is → te vaag om klaar te zetten.
        if bare_time:
            return None
        moment += timedelta(days=7)
    if (moment.date() - ontvangen.date()).days > MAX_DAYS_AHEAD:
        return None
    return moment


def _snippet(body: str, limiet: int = 160) -> str:
    zin = re.sub(r"\s+", " ", body or "").strip()
    return zin if len(zin) <= limiet else zin[: limiet - 1].rstrip() + "…"


def detect(subject: str | None, body: str | None, received_at: datetime, tz) -> dict | None:
    """Bevat deze mail een bevestigd moment? Zo ja: {"start", "text"}; anders None.

    Streng: instemming én een concreet moment moeten er allebei staan."""
    tekst = f"{subject or ''}\n{body or ''}"
    if not _ACCEPTS.search(tekst):
        return None
    start = parse_moment(f"{subject or ''}\n{body or ''}", received_at, tz)
    if start is None:
        return None
    return {"start": start, "text": _snippet(body or subject or "")}


# ── De brug naar een postvak ─────────────────────────────────────────────────
def enabled_for(account) -> bool:
    """Klaarzetten hoort bij de agenda-functie: wie momenten laat voorstellen, wil een
    bevestiging ook klaargezet zien. Zelfde opt-in als `app/scheduling.py`."""
    if account is None:
        return False
    if account.is_shared:
        return bool(getattr(account.settings, "propose_slots_enabled", False))
    owner = account.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "propose_slots_enabled", False))

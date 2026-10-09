"""Vrije momenten voorstellen in een concept.

"Wanneer past het jou?" is bij een KMO een groot deel van het mailverkeer, en precies
het soort mail waar een concept zonder agenda niets waard is: het model kan wel een
vriendelijke zin schrijven, maar geen tijdstip noemen zonder het te verzinnen. Vandaar
dat de vrije momenten als gegevens in de prompt gaan, net als de bedrijfskennis — het
model kiest eruit, het bedenkt ze niet.

Alles hier is rekenwerk op tijdstippen: geen Graph, geen database. Het uitlezen van de
agenda zit in de provider, het beoordelen van de mail in `looks_like_scheduling`, en wat
daartussen ligt — welke gaten in een dag bruikbaar zijn — staat hieronder en is dus zonder
mailbox te testen.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

# Otto stelt er hoogstens een handvol voor. Een lijst van twintig momenten is geen
# voorstel meer maar huiswerk voor de ontvanger.
MAX_SLOTS = 3
DEFAULT_DURATION_MIN = 30
# Graph levert vrij/bezet in blokken; een halfuur is de fijnste indeling die getSchedule
# zonder omwegen geeft en fijner heeft voor een afspraakvoorstel geen zin.
SLOT_MINUTES = 30
# Niet vandaag over een kwartier voorstellen: de ontvanger moet het nog kunnen lezen.
MIN_LEAD_HOURS = 2

# Woorden waarmee iemand om een moment vraagt. Bewust breed in drie talen — dit filter
# bepaalt alleen óf we de agenda raadplegen, en een keer te veel kijken kost een
# Graph-call, terwijl een keer te weinig het concept waardeloos maakt.
_ASKS = re.compile(
    r"\b("
    r"afspraak|afspreken|inplannen|inplanning|agenda|vergader\w*|meeting|"
    r"beschikbaar\w*|beschikbaarheid|wanneer\s+(past|kan|schikt|zou)|"
    r"tijdstip|moment\s+(vinden|prikken)|datum\s+prikken|call|videocall|"
    r"bellen|telefonisch|samenzitten|langskomen|"
    r"available|availability|schedule|reschedule|book\s+a\s+(call|meeting)|"
    r"rendez-vous|disponibilit\w*|planifier"
    r")\b",
    re.IGNORECASE,
)


def looks_like_scheduling(subject: str | None, body: str | None) -> bool:
    """Vraagt deze mail om een moment? Alleen dan is de agenda de moeite waard."""
    return bool(_ASKS.search(f"{subject or ''}\n{(body or '')[:2000]}"))


# ── Werkuren ─────────────────────────────────────────────────────────────────
class WorkingHours:
    """Wanneer iemand bereikbaar is. Standaard 9–17 op weekdagen.

    Outlook levert dit zelf via mailboxSettings; die waarden zijn beter dan wat wij
    zouden gokken, dus de provider vult ze in als hij ze heeft."""

    def __init__(self, start: time | None = None, end: time | None = None,
                 days: set[int] | None = None):
        self.start = start or time(9, 0)
        self.end = end or time(17, 0)
        # 0 = maandag, zoals date.weekday()
        self.days = days if days is not None else {0, 1, 2, 3, 4}

    def covers(self, moment: datetime) -> bool:
        if moment.weekday() not in self.days:
            return False
        return self.start <= moment.time() < self.end

    def window(self, day: date, tz) -> tuple[datetime, datetime]:
        return (datetime.combine(day, self.start, tzinfo=tz),
                datetime.combine(day, self.end, tzinfo=tz))


# ── Vrije momenten ───────────────────────────────────────────────────────────
def parse_availability(view: str | None, start: datetime, interval_min: int = SLOT_MINUTES):
    """Graph' `availabilityView` naar bezette periodes.

    Elk teken staat voor één interval: "0" is vrij, alles daarboven is een of andere
    vorm van bezet (voorlopig, bezet, afwezig). Alles behalve "0" telt hier als bezet —
    een voorlopige afspraak is geen vrij moment om aan een klant voor te stellen.
    """
    bezet: list[tuple[datetime, datetime]] = []
    for i, teken in enumerate(view or ""):
        if teken == "0":
            continue
        van = start + timedelta(minutes=i * interval_min)
        bezet.append((van, van + timedelta(minutes=interval_min)))
    return _merge(bezet)


def _merge(periods):
    """Aansluitende bezette blokken samenvoegen, zodat een uur vergaderen één blok is."""
    out: list[list[datetime]] = []
    for van, tot in sorted(periods):
        if out and van <= out[-1][1]:
            out[-1][1] = max(out[-1][1], tot)
        else:
            out.append([van, tot])
    return [(a, b) for a, b in out]


def free_slots(busy, now: datetime, hours: WorkingHours, *, days: int = 5,
               duration_min: int = DEFAULT_DURATION_MIN, limit: int = MAX_SLOTS):
    """De eerste `limit` momenten die vrij zijn, binnen de werkuren, vanaf `now`.

    Bewust gespreid: hoogstens één voorstel per dag. Drie momenten op dezelfde ochtend
    is geen keuze — als die dag de ontvanger niet uitkomt, ben je meteen alles kwijt.
    """
    if duration_min <= 0:
        duration_min = DEFAULT_DURATION_MIN
    vroegste = now + timedelta(hours=MIN_LEAD_HOURS)
    gekozen: list[tuple[datetime, datetime]] = []

    for dag in range(days + 1):
        if len(gekozen) >= limit:
            break
        d = (now + timedelta(days=dag)).date()
        if d.weekday() not in hours.days:
            continue
        dag_start, dag_eind = hours.window(d, now.tzinfo)
        moment = max(dag_start, vroegste)
        # Op het volgende hele halfuur beginnen: "dinsdag om 14:00" leest beter dan 13:47.
        moment = _round_up(moment)
        while moment + timedelta(minutes=duration_min) <= dag_eind:
            eind = moment + timedelta(minutes=duration_min)
            if not _overlaps(moment, eind, busy):
                gekozen.append((moment, eind))
                break  # één per dag
            moment += timedelta(minutes=SLOT_MINUTES)
    return gekozen[:limit]


def _round_up(moment: datetime) -> datetime:
    rest = (moment.minute % SLOT_MINUTES, moment.second, moment.microsecond)
    if rest == (0, 0, 0):
        return moment
    bij = SLOT_MINUTES - (moment.minute % SLOT_MINUTES)
    return (moment + timedelta(minutes=bij)).replace(second=0, microsecond=0)


def _overlaps(van: datetime, tot: datetime, busy) -> bool:
    return any(van < b_tot and b_van < tot for b_van, b_tot in busy)


# ── Voor het scherm en de prompt ─────────────────────────────────────────────
_DAGEN = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")
_MAANDEN = ("januari", "februari", "maart", "april", "mei", "juni", "juli",
            "augustus", "september", "oktober", "november", "december")


def describe(slot: tuple[datetime, datetime]) -> str:
    """"dinsdag 26 augustus, 14:00–14:30" — voluit, want een mail leest geen 26/08."""
    van, tot = slot
    return (f"{_DAGEN[van.weekday()]} {van.day} {_MAANDEN[van.month - 1]}, "
            f"{van:%H:%M}–{tot:%H:%M}")


# ── De brug naar een postvak ─────────────────────────────────────────────────
def enabled_for(account) -> bool:
    """Staat het voorstellen van momenten aan voor dit postvak? Opt-in, net als de
    andere agenda-achtige functies: je vrij/bezet is van jou, ook al gaan er alleen
    tijdstippen en nooit titels naar het model."""
    if account is None:
        return False
    if account.is_shared:
        return bool(getattr(account.settings, "propose_slots_enabled", False))
    owner = account.user
    style = getattr(owner, "style_profile", None) if owner else None
    return bool(getattr(style, "propose_slots_enabled", False))


def duration_for(account) -> int:
    """Hoe lang een voorgestelde afspraak duurt, in minuten."""
    bron = account.settings if (account is not None and account.is_shared) else (
        getattr(getattr(account, "user", None), "style_profile", None) if account else None
    )
    try:
        waarde = int(getattr(bron, "meeting_minutes", 0) or 0)
    except (TypeError, ValueError):
        waarde = 0
    return waarde if 5 <= waarde <= 480 else DEFAULT_DURATION_MIN


def slots_for(account, subject: str | None, body: str | None) -> list[str]:
    """De vrije momenten om in dit concept voor te stellen, als leesbare zinnen.

    Geeft [] terug zodra er iets niet klopt — de mail gaat niet over een afspraak, het
    staat uit, de agenda is niet te lezen. Een concept mag hier nooit op stuklopen: het
    wordt er hoogstens minder concreet van.
    """
    try:
        if not (enabled_for(account) and looks_like_scheduling(subject, body)):
            return []
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from flask import current_app

        from app.providers import get_provider

        provider = get_provider(account)
        if not provider.is_configured() or not hasattr(provider, "busy_periods"):
            return []
        try:
            tz = ZoneInfo(current_app.config.get("APP_TIMEZONE") or "Europe/Brussels")
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("Europe/Brussels")
        now = datetime.now(tz)
        einde = now + timedelta(days=7)
        uren = provider.working_hours() or WorkingHours()
        bezet = provider.busy_periods(now, einde)
        return [describe(s) for s in free_slots(bezet, now, uren, duration_min=duration_for(account))]
    except Exception:  # noqa: BLE001 — voorstellen is een bonus, nooit een voorwaarde
        return []

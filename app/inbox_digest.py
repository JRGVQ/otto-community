"""Digest-modus: je inbox als een vaste stapel in plaats van een lopende band.

Wie zijn inbox de hele dag openhoudt, triageert nooit — hij reageert. Deze modus geeft
je op vaste momenten een stapel die niet meer verandert terwijl je hem doorwerkt; wat
daarna binnenkomt, wacht op het volgende moment.

Twee dingen om eerlijk over te zijn. Otto is niet je mailprogramma: wat hier wacht,
staat gewoon in Outlook of Gmail. Dit verandert dus de volgorde waarin jíj werkt, niet
wat er bij je binnenkomt. En niets wordt tegengehouden: er staat altijd een knop om de
rest meteen te tonen, en dringende mail hoort sowieso niet in een stapel te blijven
liggen — daarvoor is de melding, niet de weergave.

Alles hier is puur rekenwerk op een tijdstip, zodat het zonder database te testen is.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

DEFAULT_HOURS = (9, 17)
MAX_SLOTS = 6  # meer dan een handvol momenten per dag is geen digest meer


def parse_hours(raw: str | None) -> list[int]:
    """"9, 17" → [9, 17]. Onzin valt terug op de standaard in plaats van te falen —
    een verkeerd getal in een instelling mag nooit een lege inbox opleveren."""
    hours = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.append(int(part))
    hours = sorted(set(hours))[:MAX_SLOTS]
    return hours or list(DEFAULT_HOURS)


def last_slot(now: datetime, hours: list[int]) -> datetime:
    """Het meest recente moment op of vóór `now` — desnoods dat van gisteren."""
    today = [datetime.combine(now.date(), time(h), tzinfo=now.tzinfo) for h in hours]
    passed = [t for t in today if t <= now]
    if passed:
        return passed[-1]
    yesterday = now.date() - timedelta(days=1)
    return datetime.combine(yesterday, time(hours[-1]), tzinfo=now.tzinfo)


def next_slot(now: datetime, hours: list[int]) -> datetime:
    """Het eerstvolgende moment ná `now` — desnoods dat van morgen."""
    today = [datetime.combine(now.date(), time(h), tzinfo=now.tzinfo) for h in hours]
    upcoming = [t for t in today if t > now]
    if upcoming:
        return upcoming[0]
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(hours[0]), tzinfo=now.tzinfo)


def split(messages, cutoff: datetime):
    """(tonen, wachten). Een bericht zonder tijdstip tonen we altijd: liever een mail te
    veel in de stapel dan een mail die nergens meer opduikt."""
    shown, held = [], []
    for m in messages:
        received = getattr(m, "received_at", None)
        if received is None:
            shown.append(m)
            continue
        # Berichten uit de database zijn naïef opgeslagen; vergelijk in dezelfde vorm.
        moment = received if received.tzinfo else received.replace(tzinfo=cutoff.tzinfo)
        (shown if moment <= cutoff else held).append(m)
    return shown, held

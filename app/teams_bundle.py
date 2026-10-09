"""Wanneer een gebundelde Teams-melding verstuurd mag worden, en hoe hij eruitziet.

Het bundelen zelf (een rij in de wachtrij zetten, de wachtrij legen) staat in
`app/tasks/teams_notify.py`. Hier staat alleen het rekenwerk: is er sinds de vorige keer
een vast moment gepasseerd, en welke tekst hoort erbij. Dat is zonder database te testen.

Dezelfde momentgedachte als de digest-inbox (6.2): op vaste tijden een overzicht in
plaats van een stroom losse meldingen. Het verschil is dat dit een kanaal voor een héél
team is — daar is één kaart per mail nog schadelijker dan in je eigen inbox.
"""
from __future__ import annotations

from datetime import datetime

# Standaardmomenten als de klant niets invult: begin van de ochtend, na de middag, eind
# van de dag. Genoeg om bij te blijven, weinig genoeg om het kanaal leefbaar te houden.
DEFAULT_HOURS = (9, 13, 17)
MAX_HOURS = 6


def parse_hours(raw: str | None) -> list[int]:
    """"9,13,17" → [9, 13, 17]. Onzin valt terug op de standaard — een verkeerd getal
    mag nooit betekenen dat het kanaal voorgoed stil blijft."""
    uren = []
    for deel in (raw or "").replace(";", ",").split(","):
        deel = deel.strip()
        if deel.isdigit() and 0 <= int(deel) <= 23:
            uren.append(int(deel))
    uren = sorted(set(uren))[:MAX_HOURS]
    return uren or list(DEFAULT_HOURS)


def _last_moment(now: datetime, hours: list[int]) -> datetime:
    """Het meest recente vaste moment op of vóór `now` (desnoods gisteravond laat)."""
    from datetime import timedelta

    vandaag = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    gepasseerd = [m for m in vandaag if m <= now]
    if gepasseerd:
        return gepasseerd[-1]
    gisteren = now - timedelta(days=1)
    return gisteren.replace(hour=hours[-1], minute=0, second=0, microsecond=0)


def due(now: datetime, hours: list[int], last_flush: datetime | None) -> bool:
    """Is het tijd om de wachtrij te legen?

    Waar als er een vast moment ligt tussen de vorige flush en nu. Zo vuurt het precies
    één keer per moment, hoe vaak de scheduler ook tikt — hetzelfde self-gate-patroon als
    de dagelijkse takenmail. Nooit geflusht? Dan wachten we op het eerstvolgende moment
    in plaats van meteen te legen, zodat een net aangezette bundel niet alsnog één losse
    melding de deur uit doet."""
    moment = _last_moment(now, hours)
    if last_flush is None:
        return False
    return last_flush < moment


def summary_card(mailbox_name: str, items: list[dict]) -> dict:
    """Eén MessageCard die alle wachtende mail opsomt.

    `items` zijn dicts met subject/sender/category_name. De kaart toont er hoogstens een
    stuk of tien met naam; de rest wordt geteld, want een kaart van honderd regels is
    net zo onleesbaar als honderd kaarten."""
    _THEME = "5B5FC7"
    toon = items[:10]
    regels = []
    for it in toon:
        onderwerp = (it.get("subject") or "(geen onderwerp)").strip()
        van = (it.get("sender") or "").strip()
        regels.append(f"- **{onderwerp}**" + (f" — {van}" if van else ""))
    rest = len(items) - len(toon)
    if rest > 0:
        regels.append(f"- … en nog {rest}")
    aantal = len(items)
    titel = f"📬 {aantal} nieuwe mail in {mailbox_name}" if aantal != 1 \
        else f"📬 Nieuwe mail in {mailbox_name}"
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _THEME,
        "summary": titel,
        "sections": [{"activityTitle": titel, "text": "\n\n".join(regels)}],
    }

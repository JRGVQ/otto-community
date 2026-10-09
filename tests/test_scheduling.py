"""Agenda: echte vrije momenten in een concept, of helemaal geen.

De grens die telt: Otto mag nooit een tijdstip noemen dat niet vrij is. Een concept dat
niets over tijd zegt kost een mailtje heen en weer; een concept dat een bezet moment
voorstelt kost een dubbele boeking en een excuus.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import scheduling as S

TZ = ZoneInfo("Europe/Brussels")
MAANDAG = datetime(2026, 8, 24, 8, 0, tzinfo=TZ)   # 24-08-2026 is een maandag


# ── Vraagt deze mail om een moment? ──────────────────────────────────────────
@pytest.mark.parametrize("tekst", [
    "Kunnen we een afspraak inplannen?",
    "Wanneer past het jou?",
    "Wanneer kan je?",
    "Heb je deze week beschikbaarheid?",
    "Zullen we even bellen?",
    "Kunnen we samenzitten volgende week?",
    "Could we schedule a call?",
    "Quelles sont vos disponibilités?",
])
def test_a_request_for_a_moment_is_recognised(tekst):
    assert S.looks_like_scheduling("", tekst) is True


@pytest.mark.parametrize("tekst", [
    "Hierbij de factuur van augustus.",
    "Bedankt voor de snelle levering!",
    "De offerte is goedgekeurd.",
])
def test_ordinary_mail_never_touches_the_calendar(tekst):
    assert S.looks_like_scheduling("", tekst) is False


def test_the_subject_counts_too():
    assert S.looks_like_scheduling("Afspraak volgende week", "Zie onder.") is True


# ── Vrij/bezet uitlezen ──────────────────────────────────────────────────────
def test_busy_blocks_are_read_and_merged():
    """Twee aansluitende halve uren zijn één vergadering, geen twee."""
    bezet = S.parse_availability("0022000", datetime(2026, 8, 24, 9, 0, tzinfo=TZ))
    assert bezet == [(datetime(2026, 8, 24, 10, 0, tzinfo=TZ),
                      datetime(2026, 8, 24, 11, 0, tzinfo=TZ))]


def test_a_tentative_appointment_counts_as_busy():
    """Graph gebruikt 1 voor voorlopig. Dat is geen moment om aan een klant te geven."""
    assert S.parse_availability("1", datetime(2026, 8, 24, 9, 0, tzinfo=TZ))


def test_an_empty_calendar_has_nothing_busy():
    assert S.parse_availability("0000", MAANDAG) == []
    assert S.parse_availability(None, MAANDAG) == []


# ── Momenten kiezen ──────────────────────────────────────────────────────────
def test_a_busy_slot_is_never_proposed():
    """De hele maandagvoormiddag zit vol; het voorstel schuift op."""
    bezet = [(datetime(2026, 8, 24, 9, 0, tzinfo=TZ), datetime(2026, 8, 24, 13, 0, tzinfo=TZ))]
    eerste = S.free_slots(bezet, MAANDAG, S.WorkingHours())[0]
    assert eerste[0] >= datetime(2026, 8, 24, 13, 0, tzinfo=TZ)


def test_proposals_are_spread_over_days():
    """Drie momenten op dezelfde ochtend is geen keuze: komt die dag niet uit, dan ben
    je meteen alles kwijt."""
    slots = S.free_slots([], MAANDAG, S.WorkingHours())
    dagen = {van.date() for van, _tot in slots}
    assert len(slots) == 3 and len(dagen) == 3


def test_nothing_is_proposed_within_the_next_two_hours():
    """De ontvanger moet de mail nog kunnen lezen."""
    slots = S.free_slots([], MAANDAG, S.WorkingHours())
    assert slots[0][0] >= MAANDAG + timedelta(hours=S.MIN_LEAD_HOURS)


def test_proposals_stay_inside_working_hours():
    slots = S.free_slots([], MAANDAG, S.WorkingHours(start=time(10, 0), end=time(12, 0)))
    for van, tot in slots:
        assert van.time() >= time(10, 0) and tot.time() <= time(12, 0)


def test_a_free_day_is_skipped():
    """Wie op woensdag vrij is, krijgt daar geen voorstel voor."""
    hours = S.WorkingHours(days={0, 1, 3, 4})      # geen woensdag
    slots = S.free_slots([], MAANDAG, hours)
    assert all(van.weekday() != 2 for van, _tot in slots)


def test_proposals_start_on_a_round_half_hour():
    """"dinsdag om 14:00" leest beter dan 13:47."""
    rommelig = datetime(2026, 8, 24, 9, 13, tzinfo=TZ)
    for van, _tot in S.free_slots([], rommelig, S.WorkingHours()):
        assert van.minute in (0, 30) and van.second == 0


def test_a_longer_meeting_needs_a_bigger_gap():
    """Een gat van een halfuur is geen plek voor een gesprek van twee uur."""
    bezet = [(datetime(2026, 8, 24, 10, 30, tzinfo=TZ), datetime(2026, 8, 24, 17, 0, tzinfo=TZ))]
    kort = S.free_slots(bezet, MAANDAG, S.WorkingHours(), duration_min=30)
    lang = S.free_slots(bezet, MAANDAG, S.WorkingHours(), duration_min=120)

    assert kort[0][0].date() == MAANDAG.date()        # 10:00–10:30 past nog
    assert lang[0][0].date() > MAANDAG.date()         # twee uur niet meer


def test_a_completely_full_week_proposes_nothing():
    """Liever niets voorstellen dan iets dat niet kan."""
    vol = [(MAANDAG, MAANDAG + timedelta(days=14))]
    assert S.free_slots(vol, MAANDAG, S.WorkingHours()) == []


def test_a_nonsense_duration_falls_back_instead_of_hanging():
    assert S.free_slots([], MAANDAG, S.WorkingHours(), duration_min=0)


# ── Hoe het in de mail komt ──────────────────────────────────────────────────
def test_a_moment_is_written_out_in_full():
    slot = (datetime(2026, 8, 25, 14, 0, tzinfo=TZ), datetime(2026, 8, 25, 14, 30, tzinfo=TZ))
    assert S.describe(slot) == "dinsdag 25 augustus, 14:00–14:30"


def test_the_prompt_forbids_inventing_a_time():
    """Zonder die instructie noemt het model een vriendelijk verzonnen tijdstip."""
    from app.ai.prompts import draft_system

    sysp = draft_system("stijl", "nl", "neutraal", slots=["dinsdag 25 augustus, 14:00–14:30"])
    assert "dinsdag 25 augustus, 14:00–14:30" in sysp
    assert "verzin" in sysp.lower() and "nooit een ander tijdstip" in sysp


def test_without_slots_the_prompt_is_unchanged():
    from app.ai.prompts import draft_system

    assert "VRIJE MOMENTEN" not in draft_system("stijl", "nl", "neutraal")


# ── De brug naar een postvak ─────────────────────────────────────────────────
class _Account:
    def __init__(self, **kw):
        self.is_shared = kw.pop("is_shared", False)
        self.settings = kw.pop("settings", None)
        self.user = kw.pop("user", None)


class _Holder:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _personal(**style):
    return _Account(user=_Holder(style_profile=_Holder(**style)))


def test_it_is_off_until_you_turn_it_on(app):
    assert S.enabled_for(_personal(propose_slots_enabled=False)) is False
    assert S.enabled_for(_personal(propose_slots_enabled=True)) is True
    assert S.enabled_for(None) is False


def test_a_shared_mailbox_follows_its_own_setting(app):
    gedeeld = _Account(is_shared=True, settings=_Holder(propose_slots_enabled=True))
    assert S.enabled_for(gedeeld) is True


@pytest.mark.parametrize("waarde,verwacht", [
    (45, 45), (None, 30), (0, 30), ("onzin", 30), (5000, 30), (-10, 30),
])
def test_the_duration_falls_back_to_something_sensible(app, waarde, verwacht):
    assert S.duration_for(_personal(meeting_minutes=waarde)) == verwacht


def test_nothing_is_proposed_when_it_is_switched_off(app):
    uit = _personal(propose_slots_enabled=False, meeting_minutes=None)
    assert S.slots_for(uit, "Afspraak?", "Wanneer past het?") == []


def test_nothing_is_proposed_for_a_mail_that_is_not_about_a_moment(app, monkeypatch):
    """Anders kost elke factuur een Graph-call."""
    aan = _personal(propose_slots_enabled=True, meeting_minutes=None)

    def boom(*a, **k):
        raise AssertionError("de agenda hoort hier niet geraadpleegd te worden")

    monkeypatch.setattr("app.providers.get_provider", boom)
    assert S.slots_for(aan, "Factuur augustus", "Zie bijlage.") == []


def test_a_broken_calendar_never_breaks_the_draft(app, monkeypatch):
    """Grondslag is een bonus: het concept wordt er hoogstens minder concreet van."""
    aan = _personal(propose_slots_enabled=True, meeting_minutes=None)

    def boom(*a, **k):
        raise RuntimeError("Graph plat")

    monkeypatch.setattr("app.providers.get_provider", boom)
    assert S.slots_for(aan, "Afspraak?", "Wanneer past het?") == []

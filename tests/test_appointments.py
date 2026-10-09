"""Een bevestigd moment herkennen en klaarzetten — nooit boeken.

De grens die telt is asymmetrisch, net als bij de toezeggingen: een gemiste bevestiging
kost niets (de gebruiker ziet de mail toch), een verzonnen afspraak kost vertrouwen. Dus
streng: instemming én een concreet moment moeten er allebei staan. En Otto boekt nooit
zelf — er komt hoogstens een concept-afspraak die de gebruiker met één klik bevestigt.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import appointment as A

TZ = ZoneInfo("Europe/Brussels")
MAANDAG = datetime(2026, 8, 24, 9, 0, tzinfo=TZ)   # een maandagochtend


# ── Wat een bevestiging is ───────────────────────────────────────────────────
@pytest.mark.parametrize("body,verwacht", [
    ("Dinsdag om 14u past prima!", datetime(2026, 8, 25, 14, 0, tzinfo=TZ)),
    ("Prima, morgen om 10u30 dan.", datetime(2026, 8, 25, 10, 30, tzinfo=TZ)),
    ("Akkoord, 26 augustus om 9u.", datetime(2026, 8, 26, 9, 0, tzinfo=TZ)),
    ("Dat werkt, woensdag 14:00.", datetime(2026, 8, 26, 14, 0, tzinfo=TZ)),
    ("Sounds good, tomorrow at 2pm.", datetime(2026, 8, 25, 14, 0, tzinfo=TZ)),
])
def test_a_confirmation_with_a_concrete_moment_is_detected(body, verwacht):
    vondst = A.detect("Re: afspraak", body, MAANDAG, TZ)
    assert vondst is not None and vondst["start"] == verwacht


@pytest.mark.parametrize("body", [
    "Dinsdag om 14u past prima!",          # instemming, maar zie hieronder: zonder cue niets
])
def test_a_moment_without_an_acceptance_cue_is_ignored(body):
    # Zelfde moment, maar de instemming eruit gehaald → puur een tijdstip is geen afspraak.
    assert A.detect("Re: afspraak", "We spreken af dinsdag om 14u.", MAANDAG, TZ) is None


@pytest.mark.parametrize("body", [
    "Prima, laat maar weten wanneer.",     # instemming zonder moment
    "Akkoord met de offerte, bedankt.",    # instemming, geen tijd
    "Ik hoor graag van je.",               # beleefdheid, geen moment
])
def test_nothing_is_staged_without_a_concrete_moment(body):
    assert A.detect("Re: afspraak", body, MAANDAG, TZ) is None


def test_a_bare_time_already_past_today_is_too_vague():
    """"Om 8u past" op een mail van 9u kan van alles betekenen — niet klaarzetten."""
    assert A.detect("Re", "Ja hoor, om 8u past.", MAANDAG, TZ) is None


def test_a_weekday_already_past_rolls_to_next_week():
    """"Maandag om 14u past" op een maandag die al 15u is → volgende maandag, niet te laat."""
    maandagmiddag = datetime(2026, 8, 24, 15, 0, tzinfo=TZ)
    v = A.detect("Re", "Maandag om 14u past.", maandagmiddag, TZ)
    assert v["start"] == datetime(2026, 8, 31, 14, 0, tzinfo=TZ)


def test_a_moment_far_in_the_future_is_ignored():
    assert A.detect("Re", "Prima, 15 oktober om 10u.", MAANDAG, TZ) is None


def test_a_bare_number_is_not_a_time():
    """"De 3 offertes passen" mag nooit 03:00 worden."""
    assert A.detect("Re", "De 3 offertes passen prima bij ons.", MAANDAG, TZ) is None


# ── Instelling ───────────────────────────────────────────────────────────────
class _Holder:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Account:
    def __init__(self, **kw):
        self.is_shared = kw.pop("is_shared", False)
        self.settings = kw.pop("settings", None)
        self.user = kw.pop("user", None)


def test_it_is_off_until_the_agenda_feature_is_on():
    uit = _Account(user=_Holder(style_profile=_Holder(propose_slots_enabled=False)))
    aan = _Account(user=_Holder(style_profile=_Holder(propose_slots_enabled=True)))
    assert A.enabled_for(uit) is False
    assert A.enabled_for(aan) is True
    assert A.enabled_for(None) is False


# ── Klaarzetten en boeken ────────────────────────────────────────────────────
@pytest.fixture()
def owner(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import StyleProfile

    user = create_company("Acme BV", "Owner", "owner@acme-ap.be", "geheim123")
    if user.style_profile is None:
        db.session.add(StyleProfile(user_id=user.id))
    db.session.flush()
    user.style_profile.propose_slots_enabled = True
    db.session.commit()
    return user


def _account(owner):
    from app.extensions import db
    from app.models import MailAccount

    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="owner@acme-ap.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def _message(acct, owner, body="Prima, dinsdag om 14u past!", thread="C1"):
    from app.extensions import db
    from app.models import Message

    msg = Message(
        organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
        provider_message_id="M1", provider_thread_id=thread,
        sender="Jan Klant", sender_email="jan@klant.be",
        subject="Re: afspraak", body=body,
        received_at=datetime(2026, 8, 24, 9, 0),
    )
    db.session.add(msg)
    db.session.commit()
    return msg


def test_a_confirmation_stages_an_appointment(app, owner):
    from app.extensions import db
    from app.models import StagedAppointment
    from app.tasks.appointments import stage_from_message

    acct = _account(owner)
    msg = _message(acct, owner)
    assert stage_from_message(msg) is True

    appt = db.session.query(StagedAppointment).one()
    assert appt.status == "pending" and appt.event_id is None
    assert appt.start_at.hour == 14 and appt.counterparty == "Jan Klant"


def test_nothing_is_staged_when_the_feature_is_off(app, owner):
    from app.extensions import db
    from app.models import StagedAppointment
    from app.tasks.appointments import stage_from_message

    owner.style_profile.propose_slots_enabled = False
    db.session.commit()
    acct = _account(owner)
    assert stage_from_message(_message(acct, owner)) is False
    assert db.session.query(StagedAppointment).count() == 0


def test_a_second_confirmation_in_the_same_thread_does_not_pile_up(app, owner):
    from app.extensions import db
    from app.models import StagedAppointment
    from app.tasks.appointments import stage_from_message

    acct = _account(owner)
    assert stage_from_message(_message(acct, owner)) is True
    # Klant schrijft nog eens in hetzelfde gesprek → geen tweede afspraak.
    tweede = _message(acct, owner, body="Ja, dinsdag om 14u is goed.")
    tweede.provider_message_id = "M2"
    db.session.commit()
    assert stage_from_message(tweede) is False
    assert db.session.query(StagedAppointment).count() == 1


class _FakeProvider:
    def __init__(self, event_id="EV-1"):
        self.event_id = event_id
        self.calls = 0

    def is_configured(self):
        return True

    def create_event(self, **kw):
        self.calls += 1
        return self.event_id


def test_confirming_books_the_event_once(app, owner, monkeypatch):
    from app.extensions import db
    from app.models import StagedAppointment
    from app.tasks.appointments import book, stage_from_message

    acct = _account(owner)
    stage_from_message(_message(acct, owner))
    appt = db.session.query(StagedAppointment).one()

    provider = _FakeProvider()
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    assert book(appt) is True
    assert appt.status == "confirmed" and appt.event_id == "EV-1" and provider.calls == 1


def test_a_failed_booking_leaves_it_pending(app, owner, monkeypatch):
    from app.extensions import db
    from app.models import StagedAppointment
    from app.tasks.appointments import book, stage_from_message

    acct = _account(owner)
    stage_from_message(_message(acct, owner))
    appt = db.session.query(StagedAppointment).one()

    monkeypatch.setattr("app.providers.get_provider", lambda a: _FakeProvider(event_id=None))
    assert book(appt) is False
    assert appt.status == "pending" and appt.event_id is None

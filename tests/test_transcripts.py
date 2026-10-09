"""Actiepunten uit een vergadertranscriptie halen — en op de takenlijst zetten.

Geen notulen-bot: Otto leest alleen transcripties die de tenant zelf al laat maken. En
streng, net als de toezeggingen: een expliciet label, een eerste-persoon-belofte of "X doet
Y" — anders geen actiepunt. Een verzonnen actiepunt kost vertrouwen in de lijst.
"""
from __future__ import annotations

from datetime import date

from app import transcripts as T

# Een woensdag, zodat "maandag" eenduidig de eerstvolgende maandag is.
WOENSDAG = date(2026, 8, 26)


# ── De extractie ─────────────────────────────────────────────────────────────
def test_a_first_person_promise_by_a_speaker_gets_that_speaker_as_owner():
    tekst = "Jan Peeters   0:03\nIk bel de klant maandag."
    items = T.extract_action_items(tekst, WOENSDAG)
    assert len(items) == 1
    assert items[0]["owner"] == "Jan Peeters"
    assert items[0]["due"] == date(2026, 8, 31)


def test_a_name_colon_line_attributes_the_action():
    items = T.extract_action_items("Marie: Ik stuur de offerte morgen.", WOENSDAG)
    assert len(items) == 1 and items[0]["owner"] == "Marie"
    assert items[0]["due"] == date(2026, 8, 27)


def test_a_third_person_assignment_is_caught():
    items = T.extract_action_items("Tom stuurt het contract deze week door.", WOENSDAG)
    assert len(items) == 1 and items[0]["owner"] == "Tom"


def test_an_explicit_label_with_a_name_in_parentheses():
    items = T.extract_action_items("Actiepunt: website nakijken (Tom)", WOENSDAG)
    assert len(items) == 1 and items[0]["owner"] == "Tom"
    assert "website nakijken" in items[0]["text"]


def test_plain_discussion_is_not_an_action_item():
    tekst = ("Jan   0:01\nWe vonden het een goed gesprek.\n"
             "Marie   0:20\nDe cijfers zagen er goed uit.")
    assert T.extract_action_items(tekst, WOENSDAG) == []


def test_the_same_action_twice_is_one_item():
    tekst = "Tom stuurt het contract.\nTom stuurt het contract."
    assert len(T.extract_action_items(tekst, WOENSDAG)) == 1


def test_a_pile_of_actions_is_capped():
    tekst = "\n".join(f"Actie: punt {i}" for i in range(40))
    assert len(T.extract_action_items(tekst, WOENSDAG)) <= T.MAX_ITEMS


def test_no_deadline_when_none_is_mentioned():
    items = T.extract_action_items("Actie: notulen rondsturen", WOENSDAG)
    assert len(items) == 1 and items[0]["due"] is None


def test_an_empty_transcript_yields_nothing():
    assert T.extract_action_items("", WOENSDAG) == []
    assert T.extract_action_items(None, WOENSDAG) == []


# ── Instelling ───────────────────────────────────────────────────────────────
class _Holder:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Account:
    def __init__(self, **kw):
        self.is_shared = kw.pop("is_shared", False)
        self.settings = kw.pop("settings", None)
        self.user = kw.pop("user", None)


def test_it_is_off_until_you_turn_it_on():
    uit = _Account(user=_Holder(style_profile=_Holder(transcripts_enabled=False)))
    aan = _Account(user=_Holder(style_profile=_Holder(transcripts_enabled=True)))
    assert T.enabled_for(uit) is False
    assert T.enabled_for(aan) is True
    assert T.enabled_for(None) is False


# ── Van actiepunt naar taak ──────────────────────────────────────────────────
import pytest  # noqa: E402


@pytest.fixture()
def owner(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import StyleProfile

    user = create_company("Acme BV", "Owner", "owner@acme-tr.be", "geheim123")
    if user.style_profile is None:
        db.session.add(StyleProfile(user_id=user.id))
    db.session.flush()
    user.style_profile.transcripts_enabled = True
    db.session.commit()
    return user


def _account(owner):
    from app.extensions import db
    from app.models import MailAccount

    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="owner@acme-tr.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def _transcript():
    return {
        "id": "TR-1", "subject": "Wekelijkse sync", "date": WOENSDAG,
        "text": "Jan   0:03\nIk bel de klant maandag.\nTom stuurt het contract.",
    }


def test_action_items_become_tasks_on_the_meeting_list(app, owner):
    from app.extensions import db
    from app.models import Task
    from app.tasks.transcripts import items_to_tasks

    acct = _account(owner)
    n = items_to_tasks(acct, _transcript())
    assert n == 2
    taken = db.session.query(Task).filter_by(list_name="Vergadering").all()
    assert len(taken) == 2
    assert any("Wekelijkse sync" in (t.context or "") for t in taken)
    assert any("Tom" in (t.context or "") for t in taken)


def test_the_same_transcript_never_yields_duplicate_tasks(app, owner):
    from app.tasks.transcripts import items_to_tasks

    acct = _account(owner)
    assert items_to_tasks(acct, _transcript()) == 2
    assert items_to_tasks(acct, _transcript()) == 0


class _FakeProvider:
    def __init__(self, transcripts):
        self.transcripts = transcripts
        self.calls = 0

    def is_configured(self):
        return True

    def list_recent_transcripts(self, days=3):
        self.calls += 1
        return self.transcripts


def test_the_sweep_pulls_from_the_provider(app, owner, monkeypatch):
    from app.tasks import transcripts as sweep

    acct = _account(owner)
    provider = _FakeProvider([_transcript()])
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    assert sweep.transcripts_for_account(acct) == 2
    assert provider.calls == 1


def test_the_sweep_does_nothing_when_disabled(app, owner, monkeypatch):
    from app.tasks import transcripts as sweep

    owner.style_profile.transcripts_enabled = False
    from app.extensions import db
    db.session.commit()
    acct = _account(owner)
    provider = _FakeProvider([_transcript()])
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    assert sweep.transcripts_for_account(acct) == 0
    assert provider.calls == 0

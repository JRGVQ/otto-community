"""Toezeggingen in verzonden mail → een taak met deadline.

De grens die telt is asymmetrisch, en de tests hieronder gaan er vooral over. Een gemiste
toezegging kost niets: je had toch al geen taak. Een verzonnen taak kost vertrouwen in de
takenlijst, en een lijst die je niet vertrouwt gebruik je niet meer. Dus: bij twijfel niets.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import commitments as C

MAANDAG = date(2026, 8, 24)
DONDERDAG = date(2026, 8, 27)


# ── Wat een toezegging is ────────────────────────────────────────────────────
@pytest.mark.parametrize("zin,verwacht", [
    ("Ik bezorg je de offerte morgen.", date(2026, 8, 25)),
    ("Ik stuur het vandaag door.", MAANDAG),
    ("Ik bel je overmorgen.", date(2026, 8, 26)),
    ("Ik kom er vrijdag op terug.", date(2026, 8, 28)),
    ("We sturen het rapport binnen 3 werkdagen.", date(2026, 8, 27)),
    ("Ik laat je binnen 2 weken iets weten.", date(2026, 9, 7)),
    ("Ik plan het volgende week maandag in.", date(2026, 8, 31)),
    ("Ik bekijk het eind van de week.", date(2026, 8, 28)),
    ("I'll send the quote tomorrow.", date(2026, 8, 25)),
])
def test_a_promise_with_a_deadline_becomes_a_task(zin, verwacht):
    gevonden = C.find(zin, MAANDAG)
    assert len(gevonden) == 1 and gevonden[0]["due"] == verwacht


@pytest.mark.parametrize("zin", [
    "Kan jij dat morgen doorsturen?",           # hij belooft, niet ik
    "Stuur je het morgen door?",
    "Ik stuur je de offerte.",                  # belofte zonder termijn
    "Morgen is het feestdag.",                  # termijn zonder belofte
    "Bedankt voor de snelle levering!",
    "De factuur van vorige week is betaald.",
])
def test_nothing_is_invented(zin):
    assert C.find(zin, MAANDAG) == []


def test_a_promise_and_a_deadline_in_different_sentences_do_not_count():
    """Precies de leesfout die een takenlijst onbruikbaar maakt."""
    tekst = "Ik stuur je de offerte. De vergadering is trouwens morgen."
    assert C.find(tekst, MAANDAG) == []


def test_an_empty_mail_yields_nothing():
    assert C.find(None, MAANDAG) == [] and C.find("", MAANDAG) == []


# ── Wanneer precies ──────────────────────────────────────────────────────────
def test_a_weekday_that_is_today_means_next_week():
    """"Ik bel je maandag" op een maandag is niet vandaag — dan was de taak meteen te laat."""
    assert C.parse_due("Ik bel je maandag.", MAANDAG) == MAANDAG + timedelta(days=7)


def test_working_days_skip_the_weekend():
    assert C.parse_due("binnen 3 werkdagen", DONDERDAG) == date(2026, 9, 1)   # di, niet zo


def test_plain_days_do_not_skip_the_weekend():
    assert C.parse_due("binnen 3 dagen", DONDERDAG) == date(2026, 8, 30)


def test_next_week_without_a_day_lands_on_monday():
    assert C.parse_due("volgende week", MAANDAG) == date(2026, 8, 31)


def test_the_deadline_is_relative_to_when_the_mail_was_sent():
    """"Morgen" in een mail van vorige week is niet morgen."""
    vorige_week = MAANDAG - timedelta(days=7)
    assert C.find("Ik stuur het morgen.", vorige_week)[0]["due"] == vorige_week + timedelta(days=1)


def test_a_deadline_far_in_the_future_is_ignored():
    """Verder dan een maand is geen "ik doe dit even" meer, en de kans op een leesfout
    is dan groter dan de winst."""
    assert C.find("Ik laat je binnen 12 weken iets weten.", MAANDAG) == []


# ── Niet te veel ─────────────────────────────────────────────────────────────
def test_two_sentences_about_the_same_day_are_one_commitment():
    tekst = "Ik stuur de offerte morgen. Ik bel je ook morgen even."
    assert len(C.find(tekst, MAANDAG)) == 1


def test_a_mail_never_produces_a_pile_of_tasks():
    tekst = " ".join(f"Ik stuur deel {i} binnen {i} dagen." for i in range(1, 9))
    assert len(C.find(tekst, MAANDAG)) <= C.MAX_TASKS_PER_MAIL


def test_a_long_sentence_is_shortened_for_the_task_title():
    lang = "Ik bezorg je " + ("heel veel details " * 30) + "morgen."
    titel = C.find(lang, MAANDAG)[0]["text"]
    assert len(titel) <= 161 and titel.endswith("…")


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
    uit = _Account(user=_Holder(style_profile=_Holder(commitments_enabled=False)))
    aan = _Account(user=_Holder(style_profile=_Holder(commitments_enabled=True)))
    assert C.enabled_for(uit) is False
    assert C.enabled_for(aan) is True
    assert C.enabled_for(None) is False


def test_a_shared_mailbox_follows_its_own_setting():
    gedeeld = _Account(is_shared=True, settings=_Holder(commitments_enabled=True))
    assert C.enabled_for(gedeeld) is True


# ── De sweep ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def owner(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import StyleProfile

    user = create_company("Acme BV", "Owner", "owner@acme-toe.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    if user.style_profile is None:
        db.session.add(StyleProfile(user_id=user.id))
    db.session.flush()
    user.style_profile.commitments_enabled = True
    db.session.commit()
    return user


def _account(owner):
    from app.extensions import db
    from app.models import MailAccount

    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="owner@acme-toe.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


class _FakeProvider:
    """Eén verzonden mail met een toezegging erin."""

    def __init__(self, mails):
        self.mails = mails
        self.calls = 0

    def is_configured(self):
        return True

    def list_recent_sent(self, days=3, limit=40):
        self.calls += 1
        return self.mails


def _sweep(monkeypatch, account, provider):
    from app.tasks import commitments as sweep

    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)
    return sweep.commitments_for_account(account)


def _mail(**kw):
    basis = {
        "provider_message_id": "SENT-1", "conversation_id": "C1",
        "subject": "Offerte", "sent_at": datetime(2026, 8, 24, 10, 0),
        "body": "Dag Jan, ik bezorg je de offerte morgen. Groeten",
    }
    basis.update(kw)
    return basis


def test_a_commitment_becomes_a_real_task(app, owner, monkeypatch):
    from app.extensions import db
    from app.models import Task

    acct = _account(owner)
    gemaakt = _sweep(monkeypatch, acct, _FakeProvider([_mail()]))

    assert gemaakt == 1
    taak = db.session.query(Task).filter_by(source_sent_message_id="SENT-1").one()
    assert taak.due_date == date(2026, 8, 25)
    assert "offerte" in taak.title.lower() and taak.user_id == owner.id


def test_the_same_mail_never_yields_a_second_task(app, owner, monkeypatch):
    """De sweep draait dagelijks; zonder dit staat elke toezegging er na een week
    zeven keer op — en dan gebruik je je takenlijst niet meer."""
    acct = _account(owner)
    provider = _FakeProvider([_mail()])

    assert _sweep(monkeypatch, acct, provider) == 1
    assert _sweep(monkeypatch, acct, provider) == 0
    assert _sweep(monkeypatch, acct, provider) == 0


def test_nothing_happens_when_the_setting_is_off(app, owner, monkeypatch):
    from app.extensions import db

    acct = _account(owner)
    owner.style_profile.commitments_enabled = False
    db.session.commit()

    provider = _FakeProvider([_mail()])
    assert _sweep(monkeypatch, acct, provider) == 0
    assert provider.calls == 0        # en de mailbox wordt niet eens bevraagd


def test_a_mail_without_a_commitment_creates_nothing(app, owner, monkeypatch):
    acct = _account(owner)
    stil = _mail(body="Bedankt voor je bestelling. Tot binnenkort!")
    assert _sweep(monkeypatch, acct, _FakeProvider([stil])) == 0


def test_a_provider_hiccup_never_breaks_the_sweep(app, owner, monkeypatch):
    class _Kapot(_FakeProvider):
        def list_recent_sent(self, days=3, limit=40):
            raise RuntimeError("Graph plat")

    acct = _account(owner)
    assert _sweep(monkeypatch, acct, _Kapot([])) == 0

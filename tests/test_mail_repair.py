"""Herstel van half binnengekomen mail + de knop "Opnieuw categoriseren".

Een partieel Graph-delta-item kon een mail zonder afzender/onderwerp/inhoud in de
inbox zetten. De regels die tellen: herstel vult alléén lege velden aan (nooit
bestaande inhoud overschrijven), hercategorisatie slaat handmatige indelingen over,
en alles is best-effort — één kapotte mail stopt de rest niet.
"""
from __future__ import annotations

import pytest

from app.providers.dto import MessageDTO


@pytest.fixture()
def setup(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import MailAccount, Message

    user = create_company("Repair BV", "Owner", "owner@repair-test.be", "geheim123")
    user.email_verified = True   # anders blokkeert de login op de verificatiemail
    user.onboarded = True
    db.session.commit()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="owner@repair-test.be", status="connected")
    db.session.add(acct)
    db.session.flush()

    def maak(pmid, **kw):
        velden = dict(organization_id=user.organization_id, user_id=user.id,
                      account_id=acct.id, provider_message_id=pmid,
                      sender="", sender_email="", initials="?",
                      subject="", snippet="", body="")
        velden.update(kw)
        m = Message(**velden)
        db.session.add(m)
        return m

    broken = maak("pm-broken")
    fine = maak("pm-fine", sender="Jan", sender_email="jan@klant.be",
                subject="Hoi", body="Inhoud")
    db.session.commit()
    return user, acct, broken, fine


class _FakeProvider:
    def __init__(self, dto):
        self.dto = dto
        self.requested = []

    def is_configured(self):
        return True

    def get_message(self, pmid):
        self.requested.append(pmid)
        return self.dto


def _dto(**kw):
    velden = dict(provider_message_id="pm-broken", thread_id=None, sender="Piet",
                  sender_email="piet@klant.be", subject="Offerte",
                  snippet="snip", body="Volledige inhoud")
    velden.update(kw)
    return MessageDTO(**velden)


# ── repair_message_details ────────────────────────────────────────────────────
def test_repair_refetches_only_broken_messages_and_fills_them(app, setup, monkeypatch):
    from app.tasks import sync

    user, acct, broken, fine = setup
    fake = _FakeProvider(_dto())
    monkeypatch.setattr(sync, "get_provider", lambda a: fake)

    assert sync.repair_message_details(acct) == 1
    assert fake.requested == ["pm-broken"]   # de gezonde mail wordt niet opnieuw opgehaald
    assert broken.sender == "Piet" and broken.sender_email == "piet@klant.be"
    assert broken.subject == "Offerte" and broken.body == "Volledige inhoud"
    assert broken.initials == "PI"
    # De gezonde mail is onaangeroerd.
    assert fine.sender == "Jan" and fine.body == "Inhoud"


def test_repair_never_overwrites_existing_content(app, setup, monkeypatch):
    from app.extensions import db
    from app.tasks import sync

    user, acct, broken, fine = setup
    # Alleen de body ontbreekt; afzender en onderwerp staan er al.
    broken.sender = "Bestaande Naam"
    broken.sender_email = "bestaand@klant.be"
    broken.subject = "Bestaand onderwerp"
    db.session.commit()
    fake = _FakeProvider(_dto())
    monkeypatch.setattr(sync, "get_provider", lambda a: fake)

    assert sync.repair_message_details(acct) == 1
    assert broken.sender == "Bestaande Naam" and broken.subject == "Bestaand onderwerp"
    assert broken.body == "Volledige inhoud"   # alleen het lege veld is aangevuld


def test_repair_survives_a_provider_failure(app, setup, monkeypatch):
    from app.tasks import sync

    user, acct, broken, fine = setup

    class _Boom(_FakeProvider):
        def get_message(self, pmid):
            raise RuntimeError("Graph plat")

    monkeypatch.setattr(sync, "get_provider", lambda a: _Boom(None))
    assert sync.repair_message_details(acct) == 0   # geen crash, gewoon niets hersteld


# ── recategorize_account ──────────────────────────────────────────────────────
def test_recategorize_skips_what_the_user_classified(app, setup, monkeypatch):
    from app.extensions import db
    from app.tasks import categorize

    user, acct, broken, fine = setup
    fine.classified_by = "user"
    db.session.commit()
    seen = []
    monkeypatch.setattr(categorize, "categorize_message", lambda mid: seen.append(mid))

    assert categorize.recategorize_account(acct.id) == 1
    assert seen == [broken.id]   # de handmatig ingedeelde mail blijft staan


def test_one_failing_message_does_not_stop_recategorisation(app, setup, monkeypatch):
    from app.tasks import categorize

    user, acct, broken, fine = setup
    calls = []

    def flaky(mid):
        calls.append(mid)
        if mid == fine.id:
            raise RuntimeError("AI-budget op")

    monkeypatch.setattr(categorize, "categorize_message", flaky)
    assert categorize.recategorize_account(acct.id) == 1   # broken lukte wél
    assert sorted(calls) == sorted([broken.id, fine.id])


# ── De knop: POST /api/accounts/recategorize ──────────────────────────────────
def _login(client):
    client.post("/auth/login", data={"email": "owner@repair-test.be",
                                     "password": "geheim123"})


def test_the_button_repairs_inline_and_starts_recategorisation(app, setup, client, monkeypatch):
    import app.tasks.sync as sync_mod
    from app.api import accounts as accounts_api

    user, acct, broken, fine = setup
    spawned = []
    monkeypatch.setattr(accounts_api, "_spawn_recategorize", lambda ids: spawned.extend(ids))
    monkeypatch.setattr(sync_mod, "get_provider", lambda a: _FakeProvider(_dto()))
    _login(client)

    r = client.post("/api/accounts/recategorize")
    data = r.get_json()
    assert data["ok"] is True and "1 mail(s) hersteld" in data["toast"]
    assert spawned == [acct.id]
    assert broken.sender_email == "piet@klant.be"


def test_the_button_without_mailbox_is_a_clear_noop(app, client):
    from app.auth.service import create_company
    from app.extensions import db

    leeg = create_company("Leeg BV", "Owner", "owner@leeg-test.be", "geheim123")
    leeg.email_verified = True
    leeg.onboarded = True
    db.session.commit()
    client.post("/auth/login", data={"email": "owner@leeg-test.be", "password": "geheim123"})
    r = client.post("/api/accounts/recategorize")
    assert r.get_json()["ok"] is True
    assert "Geen gekoppeld postvak" in r.get_json()["toast"]


# ── _persist vult lege velden van een bestaande rij aan bij hersync ───────────
def test_persist_backfills_an_existing_half_empty_row(app, setup):
    from app.tasks.sync import _persist

    user, acct, broken, fine = setup
    assert _persist(acct, _dto()) is None      # bestaand bericht → geen nieuwe rij
    assert broken.sender_email == "piet@klant.be" and broken.body == "Volledige inhoud"

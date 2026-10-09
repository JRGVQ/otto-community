"""Zelfhulp: 'alles opnieuw sorteren' herstelt leeg-opgeslagen mail + herclassificeert,
en de sync geneest een smalle-cursor vanzelf.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import Category, MailAccount, Message, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _account(user):
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="me@acme.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def _broken_msg(user, acct):
    """Een id-only opgeslagen bericht: geen afzender, geen body, foutief als spam."""
    spam = db.session.query(Category).filter_by(
        organization_id=user.organization_id).order_by(Category.position.desc()).first()
    m = Message(organization_id=user.organization_id, user_id=user.id, account_id=acct.id,
                provider_message_id="PM-broken", subject="(geen onderwerp)",
                sender="", sender_email="", body="", category_id=spam.id if spam else None)
    db.session.add(m)
    db.session.commit()
    return m


def test_recategorize_repairs_and_reclassifies(app, monkeypatch):
    user = _demo_user()
    acct = _account(user)
    msg = _broken_msg(user, acct)

    from app.providers.dto import MessageDTO
    import app.tasks.recategorize as rc

    class FakeProvider:
        def is_configured(self):
            return True

        def fetch_message(self, pmid):
            return MessageDTO(provider_message_id=pmid, thread_id="T", sender="Klant NV",
                              sender_email="klant@nv.be", subject="Echte offerteaanvraag",
                              snippet="hallo", body="Graag een offerte.")

    enqueued = []
    monkeypatch.setattr(rc, "get_provider", lambda a: FakeProvider(), raising=False)
    monkeypatch.setattr("app.providers.get_provider", lambda a: FakeProvider())
    monkeypatch.setattr("app.tasks.enqueue.enqueue", lambda fn, *a, **k: enqueued.append(a))

    n = rc.recategorize_account(acct.id)
    assert n == 1
    db.session.refresh(msg)
    # Inhoud hersteld…
    assert msg.sender_email == "klant@nv.be" and msg.subject == "Echte offerteaanvraag"
    assert msg.body == "Graag een offerte."
    # …categorie gewist zodat de pijplijn opnieuw sorteert, en opnieuw ingepland.
    assert msg.category_id is None
    assert enqueued == [(msg.id,)]


def test_recategorize_skips_shared_and_seed(app, monkeypatch):
    user = _demo_user()
    shared = MailAccount(user_id=user.id, organization_id=user.organization_id,
                         provider="outlook", email="team@acme.be", status="connected",
                         mailbox_type="shared", target_address="team@acme.be")
    db.session.add(shared)
    db.session.commit()
    from app.tasks.recategorize import recategorize_user

    # Geen persoonlijk postvak met echte mail → niets te doen, geen fout.
    assert recategorize_user(user) == 0


def test_recategorize_endpoint(app, auth, monkeypatch):
    calls = []
    monkeypatch.setattr("app.tasks.recategorize.recategorize_user",
                        lambda user, **k: calls.append(user.id) or 5)
    r = auth.post("/api/settings/recategorize")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and "5" in body["toast"]
    assert len(calls) == 1


def test_sync_self_heals_a_narrow_cursor(app, monkeypatch):
    """Een postvak met een smalle cursor herbouwt de baseline bij de volgende sync."""
    user = _demo_user()
    acct = _account(user)
    acct.sync_cursor = "https://graph.microsoft.com/v1.0/delta?$select=id&$deltatoken=old"
    db.session.commit()

    used = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def delta_baseline(self):
            return "https://graph.microsoft.com/v1.0/delta?$select=id,subject,from,body&$deltatoken=new"

        def fetch_new(self, cursor):
            used["cursor"] = cursor
            return [], cursor

    import app.tasks.sync as sync
    monkeypatch.setattr(sync, "get_provider", lambda a: FakeProvider())
    monkeypatch.setattr(sync, "reconcile_inbox", lambda a, p: 0)

    sync.sync_account(acct.id)
    db.session.refresh(acct)
    # Cursor is vervangen door de volledige-veldenbaseline, en fetch_new kreeg die.
    assert "subject" in acct.sync_cursor and "deltatoken=new" in acct.sync_cursor
    assert "subject" in used["cursor"]

"""Snippets: herbruikbare tekstblokken voor gedeelde mailboxen (Teams fase 5).

Naast het gewone gedrag bewaken deze tests vooral de grens: blokken van één gedeelde
mailbox mogen niet zichtbaar of verwijderbaar zijn voor iemand van een ander team.
"""
from __future__ import annotations

import pytest

from app import snippets as svc
from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, MailboxMember, Role, Snippet, User
from app.models.user import initials_for


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-sn.be", "geheim123")
    # Teams is Enterprise-only (24/08); deze schermen vereisen het plan.
    if user.organization.license:
        user.organization.license.plan = "Enterprise"
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user


def _member(org, email="collega@acme-sn.be"):
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    u = User(email=email, name="Collega", initials=initials_for("Collega"), avatar_color="#111",
             organization_id=org.id, role_id=role.id if role else None, status="Actief",
             email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()
    return u


def _shared(owner, email="team@acme-sn.be", members=()):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id, provider="outlook",
                       email=email, status="connected", mailbox_type="shared")
    db.session.add(acct)
    db.session.flush()
    for u in (owner, *members):
        db.session.add(MailboxMember(mail_account_id=acct.id, user_id=u.id, role="agent"))
    db.session.commit()
    return acct


# ── Service ──────────────────────────────────────────────────────────────────
def test_org_wide_snippet_applies_everywhere(app, owner):
    org = owner.organization
    svc.create(org, title="Openingsuren", body="We zijn open van 9 tot 18u.")
    acct = _shared(owner)
    titles = [s.title for s in svc.for_account(org, acct)]
    assert titles == ["Openingsuren"]


def test_mailbox_snippet_only_shows_in_its_own_mailbox(app, owner):
    org = owner.organization
    support = _shared(owner, "support@acme-sn.be")
    sales = _shared(owner, "sales@acme-sn.be")
    svc.create(org, title="Retour", body="Je kan binnen 14 dagen retourneren.", account=support)

    assert [s.title for s in svc.for_account(org, support)] == ["Retour"]
    assert svc.for_account(org, sales) == []


def test_both_kinds_are_merged_org_wide_first(app, owner):
    org = owner.organization
    acct = _shared(owner)
    svc.create(org, title="Alg", body="organisatiebreed")
    svc.create(org, title="Mailbox", body="alleen hier", account=acct)
    assert [s.title for s in svc.for_account(org, acct)] == ["Alg", "Mailbox"]


def test_create_refuses_empty_input(app, owner):
    org = owner.organization
    assert svc.create(org, title="", body="tekst") is None
    assert svc.create(org, title="Titel", body="   ") is None
    assert db.session.query(Snippet).count() == 0


def test_mark_used_counts_atomically(app, owner):
    s = svc.create(owner.organization, title="T", body="B")
    svc.mark_used(s)
    svc.mark_used(s)
    db.session.expire_all()
    assert db.session.get(Snippet, s.id).used_count == 2


def test_manageable_refuses_another_organisation(app, owner):
    other = create_company("Rivaal NV", "R", "r@rivaal-sn.be", "geheim123").organization
    s = svc.create(other, title="Van de rivaal", body="x")
    assert svc.manageable(owner, s) is False


def test_manageable_refuses_a_mailbox_you_are_not_in(app, owner):
    org = owner.organization
    outsider = _member(org, "buiten@acme-sn.be")
    acct = _shared(owner, "support@acme-sn.be")   # outsider is geen lid
    s = svc.create(org, title="Retour", body="x", account=acct)
    assert svc.manageable(owner, s) is True
    assert svc.manageable(outsider, s) is False


# ── API ──────────────────────────────────────────────────────────────────────
def _login(client, email="owner@acme-sn.be"):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return client


def test_api_lists_snippets_for_a_message(app, client, owner):
    from app.models import Message

    org = owner.organization
    acct = _shared(owner)
    svc.create(org, title="Openingsuren", body="9 tot 18u")
    svc.create(org, title="Retour", body="14 dagen", account=acct)
    msg = Message(organization_id=org.id, user_id=owner.id, account_id=acct.id,
                  sender="Klant", sender_email="k@voorbeeld.be", subject="Vraag")
    db.session.add(msg)
    db.session.commit()

    resp = _login(client).get(f"/api/snippets?message_id={msg.id}")
    assert resp.status_code == 200
    titles = [s["title"] for s in resp.get_json()["snippets"]]
    assert titles == ["Openingsuren", "Retour"]


def test_api_create_and_delete(app, client, owner):
    acct = _shared(owner)
    c = _login(client)
    resp = c.post("/api/snippets", data={"account_id": acct.id, "title": "Retour", "body": "14 dagen"})
    assert resp.status_code == 200
    s = db.session.query(Snippet).one()
    assert s.account_id == acct.id

    assert c.delete(f"/api/snippets/{s.id}").status_code == 200
    assert db.session.query(Snippet).count() == 0


def test_api_create_refuses_empty(app, client, owner):
    acct = _shared(owner)
    resp = _login(client).post("/api/snippets", data={"account_id": acct.id, "title": "", "body": ""})
    assert resp.status_code == 400


def test_api_refuses_a_mailbox_you_are_not_in(app, client, owner):
    org = owner.organization
    outsider = _member(org, "buiten@acme-sn.be")
    acct = _shared(owner, "support@acme-sn.be")
    c = _login(client, outsider.email)
    assert c.post("/api/snippets", data={"account_id": acct.id, "title": "T", "body": "B"}).status_code == 403


def test_api_delete_refuses_another_organisation(app, client, owner):
    other = create_company("Rivaal NV", "R", "r@rivaal-sn.be", "geheim123").organization
    s = svc.create(other, title="Van de rivaal", body="x")
    assert _login(client).delete(f"/api/snippets/{s.id}").status_code == 404


def test_api_requires_login(app, client, owner):
    assert client.get("/api/snippets").status_code in (302, 401)


def test_used_endpoint_counts(app, client, owner):
    s = svc.create(owner.organization, title="T", body="B")
    assert _login(client).post(f"/api/snippets/{s.id}/used").status_code == 200
    db.session.expire_all()
    assert db.session.get(Snippet, s.id).used_count == 1


# ── Scherm ───────────────────────────────────────────────────────────────────
def test_team_screen_shows_snippets(app, client, owner):
    acct = _shared(owner)
    svc.create(owner.organization, title="Openingsuren", body="9 tot 18u", account=acct)
    html = _login(client).get(f"/app/teams/{acct.id}/settings").get_data(as_text=True)
    assert "Snippets" in html and "Openingsuren" in html


def test_inbox_carries_the_snippet_bar(app, client, owner):
    html = _login(client).get("/app/inbox").get_data(as_text=True)
    assert "js/snippets.js" in html


def test_deep_delete_purges_snippets(app, owner):
    """snippets heeft een non-nullable org-FK: zonder purge faalt een definitieve wis."""
    from app.tenancy import delete_org_deep

    oid = owner.organization_id
    svc.create(owner.organization, title="T", body="B")
    delete_org_deep(owner.organization)
    db.session.commit()
    assert db.session.query(Snippet).filter_by(organization_id=oid).count() == 0

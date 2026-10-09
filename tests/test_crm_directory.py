"""Otto's eigen CRM: passieve collectie, deterministische standaardcategorie,
projecten + mail→project.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import (
    Category,
    Company,
    Contact,
    MailAccount,
    Message,
    ProjectMessage,
    User,
)
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _msg(user, sender_email, sender="Iemand", subject="Hallo"):
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="me@acme.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    m = Message(organization_id=user.organization_id, user_id=user.id, account_id=acct.id,
                sender=sender, sender_email=sender_email, subject=subject, body="tekst")
    db.session.add(m)
    db.session.commit()
    return m


# ── passieve collectie ──────────────────────────────────────────
def test_collect_builds_company_and_contact(app):
    user = _demo_user()
    m = _msg(user, "jan@klant.be", "Jan Klant")

    from app.directory import collect_from_message
    collect_from_message(m)
    db.session.commit()

    contact = db.session.query(Contact).filter_by(email="jan@klant.be").one()
    assert contact.name == "Jan Klant" and contact.message_count == 1
    company = db.session.query(Company).filter_by(domain="klant.be").one()
    assert contact.company_id == company.id


# ── deterministische standaardcategorie (contact wint van bedrijf) ────────────
def test_default_category_contact_beats_company(app):
    user = _demo_user()
    cats = db.session.query(Category).filter_by(
        organization_id=user.organization_id).order_by(Category.position).all()
    company_cat, contact_cat = cats[0], cats[1]

    company = Company(organization_id=user.organization_id, domain="klant.be",
                      default_category_id=company_cat.id)
    db.session.add(company)
    db.session.flush()
    db.session.add(Contact(organization_id=user.organization_id, company_id=company.id,
                           email="jan@klant.be", default_category_id=contact_cat.id))
    db.session.commit()

    from app.directory import default_category_for
    m = _msg(user, "jan@klant.be")
    assert default_category_for(m).id == contact_cat.id           # contact wint

    # Zonder contact-override valt hij terug op de bedrijfsstandaard.
    other = _msg(user, "piet@klant.be")
    assert default_category_for(other).id == company_cat.id


def test_categorize_uses_directory_override_without_ai(app, monkeypatch):
    user = _demo_user()
    cat = db.session.query(Category).filter_by(
        organization_id=user.organization_id).order_by(Category.position).first()
    company = Company(organization_id=user.organization_id, domain="klant.be",
                      default_category_id=cat.id)
    db.session.add(company)
    db.session.commit()
    m = _msg(user, "jan@klant.be")

    # Als de AI toch werd aangeroepen, faalt de test luid.
    import app.tasks.categorize as cz
    monkeypatch.setattr(cz, "get_service_for",
                        lambda org: (_ for _ in ()).throw(AssertionError("AI mag niet draaien")))
    monkeypatch.setattr(cz, "write_label", lambda msg: None)

    result = cz.categorize_message(m.id)
    assert result["source"] == "directory"
    db.session.refresh(m)
    assert m.category_id == cat.id and m.classified_by == "directory"


# ── projecten + mail→project (API, Enterprise-gated) ─────────────────────────
def test_project_crud_and_link_message(app, auth):
    user = _demo_user()
    m = _msg(user, "jan@klant.be")

    r = auth.post("/api/crm/projects", json={"name": "Website Acme"})
    assert r.status_code == 200
    pid = r.get_json()["id"]

    r = auth.post(f"/api/crm/projects/{pid}/messages", json={"message_id": m.id})
    assert r.status_code == 200
    assert db.session.query(ProjectMessage).filter_by(project_id=pid, message_id=m.id).count() == 1

    # Idempotent: nog eens koppelen maakt geen tweede rij.
    auth.post(f"/api/crm/projects/{pid}/messages", json={"message_id": m.id})
    assert db.session.query(ProjectMessage).filter_by(project_id=pid).count() == 1

    r = auth.post(f"/api/crm/projects/{pid}/messages/{m.id}/delete")
    assert r.status_code == 200
    assert db.session.query(ProjectMessage).filter_by(project_id=pid).count() == 0


def test_set_default_category_endpoint(app, auth):
    user = _demo_user()
    cat = db.session.query(Category).filter_by(organization_id=user.organization_id).first()
    company = Company(organization_id=user.organization_id, domain="klant.be")
    db.session.add(company)
    db.session.commit()
    r = auth.post(f"/api/crm/company/{company.id}/category", json={"category_id": cat.id})
    assert r.status_code == 200
    db.session.refresh(company)
    assert company.default_category_id == cat.id


# ── plan-gating: CRM volgt de Teams-entitlement (Business/Enterprise) ─────────

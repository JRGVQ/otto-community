"""Centraal handtekening-beheer met IT-goedkeuringsflow.

Bewaakte beloftes:
1. is_central volgt de org-schakelaar;
2. in centrale modus wordt een profielveld-wijziging een VOORSTEL (pending) i.p.v. direct
   opgeslagen; persoonlijke variabelen blijven wel direct;
3. zonder centrale modus blijft het oude gedrag (direct opslaan);
4. goedkeuren past de voorgestelde waarden toe op de echte velden; afwijzen niet;
5. de beheerroutes staan achter manageOrgCats en zijn org-gescoped.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import (
    CloudSignatureSetting,
    SignatureChangeRequest,
    SignatureVariable,
    SignatureVariableValue,
    User,
)
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _set_central(org_id, on=True):
    row = db.session.query(CloudSignatureSetting).filter_by(organization_id=org_id).first()
    if row is None:
        row = CloudSignatureSetting(organization_id=org_id)
        db.session.add(row)
    row.central_management = on
    db.session.commit()


# ── is_central ───────────────────────────────────────────────────────────────
def test_is_central_follows_switch(app):
    from app.signature_central import is_central

    user = _demo_user()
    assert is_central(user.organization) is False
    _set_central(user.organization_id, True)
    assert is_central(user.organization) is True


# ── save_profile: voorstel vs direct ─────────────────────────────────────────
def test_open_mode_saves_directly(app, auth):
    user = _demo_user()
    user.job_title = "Oud"
    db.session.commit()
    auth.post("/app/settings/profiel", data={"job_title": "Nieuw", "department": "",
              "office_location": "", "phone": "", "mobile": ""}, follow_redirects=True)
    db.session.refresh(user)
    assert user.job_title == "Nieuw"
    assert db.session.query(SignatureChangeRequest).count() == 0


def test_central_mode_creates_proposal_not_direct(app, auth):
    user = _demo_user()
    user.job_title = "Oud"
    db.session.commit()
    _set_central(user.organization_id, True)

    auth.post("/app/settings/profiel", data={"job_title": "Voorgesteld", "department": "",
              "office_location": "", "phone": "+32 9 111", "mobile": ""}, follow_redirects=True)
    db.session.refresh(user)
    # Veld NIET direct gewijzigd; wél een pending voorstel met de gewijzigde velden.
    assert user.job_title == "Oud"
    req = db.session.query(SignatureChangeRequest).filter_by(user_id=user.id, status="pending").one()
    assert req.payload.get("job_title") == "Voorgesteld"
    assert req.payload.get("phone") == "+32 9 111"
    assert "department" not in req.payload  # onveranderd → niet in het voorstel

    # Opnieuw indienen vervangt het lopende voorstel (geen tweede rij).
    auth.post("/app/settings/profiel", data={"job_title": "Anders", "department": "",
              "office_location": "", "phone": "", "mobile": ""}, follow_redirects=True)
    assert db.session.query(SignatureChangeRequest).filter_by(status="pending").count() == 1


def test_central_mode_personal_var_still_direct(app, auth):
    user = _demo_user()
    _set_central(user.organization_id, True)
    pv = SignatureVariable(organization_id=user.organization_id, user_id=user.id,
                           key="linkedin", label="LinkedIn")
    db.session.add(pv)
    db.session.commit()
    auth.post("/app/settings/profiel", data={"job_title": "", "department": "", "office_location": "",
              "phone": "", "mobile": "", f"var-{pv.id}": "https://x"}, follow_redirects=True)
    val = db.session.query(SignatureVariableValue).filter_by(variable_id=pv.id, user_id=user.id).one()
    assert val.value == "https://x"  # persoonlijke variabele blijft direct opgeslagen


# ── goedkeuren / afwijzen ────────────────────────────────────────────────────
def test_approve_applies_values(app, auth):
    user = _demo_user()
    user.job_title = "Oud"
    var = SignatureVariable(organization_id=user.organization_id, user_id=None, key="lijn", label="Lijn 2")
    db.session.add(var)
    db.session.flush()
    req = SignatureChangeRequest(organization_id=user.organization_id, user_id=user.id,
                                 payload={"job_title": "Goedgekeurd", f"var-{var.id}": "AB-1"}, status="pending")
    db.session.add(req)
    db.session.commit()

    r = auth.post(f"/admin/signatures/requests/{req.id}/approve", follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(user)
    db.session.refresh(req)
    assert user.job_title == "Goedgekeurd" and req.status == "approved" and req.reviewed_by == user.id
    val = db.session.query(SignatureVariableValue).filter_by(variable_id=var.id, user_id=user.id).one()
    assert val.value == "AB-1"


def test_reject_does_not_apply(app, auth):
    user = _demo_user()
    user.job_title = "Oud"
    req = SignatureChangeRequest(organization_id=user.organization_id, user_id=user.id,
                                 payload={"job_title": "Afgewezen"}, status="pending")
    db.session.add(req)
    db.session.commit()
    auth.post(f"/admin/signatures/requests/{req.id}/reject", data={"note": "nee"}, follow_redirects=True)
    db.session.refresh(user)
    db.session.refresh(req)
    assert user.job_title == "Oud" and req.status == "rejected" and req.note == "nee"


def test_toggle_and_scope(app, auth):
    user = _demo_user()
    auth.post("/admin/signatures/central/save", data={"central_management": "on"}, follow_redirects=True)
    row = db.session.query(CloudSignatureSetting).filter_by(organization_id=user.organization_id).one()
    assert row.central_management is True

    # Andermans voorstel goedkeuren → 404.
    other = create_company("Ander BV", "A B", "a@ander-c.be", "geheim123")
    oreq = SignatureChangeRequest(organization_id=other.organization_id, user_id=other.id,
                                  payload={"job_title": "x"}, status="pending")
    db.session.add(oreq)
    db.session.commit()
    assert auth.post(f"/admin/signatures/requests/{oreq.id}/approve").status_code == 404

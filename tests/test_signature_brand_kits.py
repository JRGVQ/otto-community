"""Brand kits: centrale huisstijl (logo + accentkleur) per organisatie/domein.

Een tenant met meerdere merken/domeinen zet per domein een eigen brand kit; de {{logo}}-
placeholder resolvet naar het logo van het verzenddomein (of de org-standaard).
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import BrandKit, MailAccount, User
from app.seed.demo_data import DEMO_PASSWORD
from app.signatures import brand_kit_for, placeholder_values


@pytest.fixture()
def owner(app):
    user = create_company("Merk BV", "Mar Merk", "mar@merk-a.be", "geheim123")
    user.email_verified = user.onboarded = True
    db.session.commit()
    return user


def _kit(owner, domain=None, logo="https://x/logo.png", color="#112233", name=None):
    k = BrandKit(organization_id=owner.organization_id, domain=domain, name=name,
                 logo_url=logo, accent_color=color)
    db.session.add(k)
    db.session.commit()
    return k


def _account(owner, email="mar@merk-a.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    return acct


def test_brand_kit_for_domain_beats_default(app, owner):
    _kit(owner, domain=None, logo="https://x/default.png")
    _kit(owner, domain="merk-a.be", logo="https://x/merk-a.png")
    assert brand_kit_for(owner.organization, "merk-a.be").logo_url == "https://x/merk-a.png"
    assert brand_kit_for(owner.organization, "onbekend.be").logo_url == "https://x/default.png"
    assert brand_kit_for(owner.organization, None).logo_url == "https://x/default.png"


def test_logo_placeholder_resolves_per_domain(app, owner):
    _kit(owner, domain="merk-a.be", logo="https://x/merk-a.png", color="#abcdef")
    acct = _account(owner, email="mar@merk-a.be")
    vals = placeholder_values(owner, acct)
    assert vals["logo"] == "https://x/merk-a.png"
    assert vals["merk_kleur"] == "#abcdef"


def test_logo_empty_without_kit(app, owner):
    acct = _account(owner)
    assert placeholder_values(owner, acct).get("logo") == ""


# ── routes ───────────────────────────────────────────────────────────────────
@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_brand_routes_save_and_delete(app, auth):
    user = _demo_user()
    r = auth.post("/admin/signatures/brand/save", data={
        "name": "Merk A", "domain": "merk-a.be", "logo_url": "https://x/a.png",
        "accent_color": "#123456"}, follow_redirects=True)
    assert r.status_code == 200
    kit = db.session.query(BrandKit).filter_by(organization_id=user.organization_id, domain="merk-a.be").one()
    assert kit.logo_url == "https://x/a.png" and kit.accent_color == "#123456"

    # Eén kit per (org, domein): opnieuw opslaan werkt bij i.p.v. dubbel.
    auth.post("/admin/signatures/brand/save", data={"domain": "merk-a.be",
              "logo_url": "https://x/a2.png"}, follow_redirects=True)
    assert db.session.query(BrandKit).filter_by(domain="merk-a.be").count() == 1

    # Delete van een andere org → 404.
    other = create_company("X BV", "X Y", "x@x-brand.be", "geheim123")
    ok = BrandKit(organization_id=other.organization_id, domain=None, logo_url="https://x/o.png")
    db.session.add(ok)
    db.session.commit()
    assert auth.post(f"/admin/signatures/brand/{ok.id}/delete").status_code == 404
    assert auth.post(f"/admin/signatures/brand/{kit.id}/delete", follow_redirects=True).status_code == 200
    assert db.session.get(BrandKit, kit.id) is None

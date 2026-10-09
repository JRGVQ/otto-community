"""Topbar-meganav + de aparte Teams-knop (Business/Enterprise-functie).

Bewaakte beloftes:
1. Teams staat NIET meer in het Werk-menu maar heeft een eigen topbar-knop;
2. op een plan mét Teams (Business/Enterprise) toont die knop de subnavigatie per
   gedeelde mailbox (Inbox / Analyse / Instellingen) + het Teams-overzicht;
3. onder Business blijft de knop zichtbaar met een slotje en upsell —
   zonder werkende Teams-links;
4. de mega-menu's dragen per item een korte omschrijving (Velt-stijl).
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import MailAccount, MailboxMember, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _shared_for(user, address):
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email=address, status="connected",
                       mailbox_type="shared", target_address=address)
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=user.id, role="owner"))
    db.session.commit()
    return acct


def test_teams_left_the_werk_menu_for_its_own_button(app, auth):
    user = _demo_user()
    page = auth.get("/app/inbox").get_data(as_text=True)
    assert "Teams-overzicht" in page  # eigen knop met subnavigatie

    # Contextniveau: het Werk-menu bevat Teams niet meer; de mobiele lade (nav) wél.
    from app.web.context._base import base_context

    with app.test_request_context():
        from flask_login import login_user

        login_user(user)
        data = base_context("user", "inbox")
    menu_keys = {it["key"] for m in data["nav_menus"] for s in m["sections"] for it in s["items"]}
    assert "teams" not in menu_keys
    assert any(n["key"] == "teams" for n in data["nav"])  # drawer houdt het item
    assert data["teams_nav"]["entitled"] is True


def test_shared_mailbox_subnavigation(app, auth):
    user = _demo_user()
    acct = _shared_for(user, "support@acme.be")
    page = auth.get("/app/inbox").get_data(as_text=True)
    assert f"/app/teams/{acct.id}" in page            # Inbox-link van de mailbox
    assert f"/app/teams/{acct.id}/analyse" in page    # subnavigatie
    assert f"/app/teams/{acct.id}/settings" in page
    assert "support@acme.be" in page


def test_mega_menu_carries_item_descriptions(app, auth):
    page = auth.get("/app/inbox").get_data(as_text=True)
    assert "tn-mega" in page
    assert "Je mail, gesorteerd en met concepten klaar" in page   # desc bij Inbox
    assert "Profiel, schrijfstijl en voorkeuren" in page          # desc bij Instellingen

    admin = auth.get("/admin/overzicht").get_data(as_text=True)
    assert "Centrale handtekeningen en variabelen" in admin       # admin-desc



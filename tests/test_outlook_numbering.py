"""Nummers voor categorieën in Outlook.

Outlook sorteert categorieën alfabetisch; een nummer ervoor laat de belangrijkste
bovenaan staan. Deze tests bewaken de naamgeving, dat de volgorde uit Otto meekomt, en
dat er nooit dubbel genummerd wordt.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import Category
from app.outlook_labels import all_labels, label_for, numbering_enabled, strip_number


@pytest.fixture()
def org(app):
    user = create_company("Acme BV", "Owner", "owner@acme-num.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user.organization


def _cat(org, name, position):
    c = Category(organization_id=org.id, key=name.lower().replace(" ", "_"),
                 name=name, position=position)
    db.session.add(c)
    db.session.commit()
    return c


# ── Naamgeving ───────────────────────────────────────────────────────────────
def test_without_numbering_the_plain_name_is_used(app, org):
    cat = _cat(org, "Te beantwoorden", 0)
    assert numbering_enabled(org) is False  # standaard uit
    assert label_for(cat, org) == "Te beantwoorden"


def test_numbering_follows_the_category_order(app, org):
    eerste = _cat(org, "Te beantwoorden", 0)
    tweede = _cat(org, "FYI", 1)
    org.outlook_category_numbering = True
    db.session.commit()

    assert label_for(eerste, org) == "1. Te beantwoorden"
    assert label_for(tweede, org) == "2. FYI"


def test_a_name_is_never_numbered_twice(app, org):
    """Anders krijg je '1. 2. Naam' zodra een al genummerde naam opnieuw langskomt."""
    cat = _cat(org, "2. Te beantwoorden", 0)
    org.outlook_category_numbering = True
    db.session.commit()
    assert label_for(cat, org) == "1. Te beantwoorden"


def test_strip_number_removes_only_a_leading_number():
    assert strip_number("1. Te beantwoorden") == "Te beantwoorden"
    assert strip_number("10. FYI") == "FYI"
    assert strip_number("Factuur 2024") == "Factuur 2024"   # geen leidend nummer
    assert strip_number("Q1. rapport") == "Q1. rapport"     # geen kaal getal


def test_reordering_categories_renumbers_them(app, org):
    a = _cat(org, "Te beantwoorden", 0)
    b = _cat(org, "FYI", 1)
    org.outlook_category_numbering = True
    db.session.commit()
    assert label_for(a, org) == "1. Te beantwoorden"

    a.position, b.position = 1, 0   # omgewisseld in Otto
    db.session.commit()
    assert label_for(a, org) == "2. Te beantwoorden"
    assert label_for(b, org) == "1. FYI"


def test_all_labels_lists_both_forms(app, org):
    _cat(org, "Te beantwoorden", 0)
    _cat(org, "FYI", 1)
    genummerd = all_labels(org, numbered=True)
    kaal = all_labels(org, numbered=False)
    assert "1. Te beantwoorden" in genummerd and "2. FYI" in genummerd
    assert "Te beantwoorden" in kaal and "FYI" in kaal


# ── Push naar Outlook ────────────────────────────────────────────────────────
def test_the_pushed_label_follows_the_setting(app, org, monkeypatch):
    from app.models import MailAccount, Message, User
    from app.tasks.writeback import write_label

    owner = db.session.get(User, org.owner_user_id)
    acct = MailAccount(user_id=owner.id, organization_id=org.id, provider="outlook",
                       email="owner@acme-num.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    cat = _cat(org, "Te beantwoorden", 0)
    msg = Message(organization_id=org.id, user_id=owner.id, account_id=acct.id,
                  provider_message_id="m-1", sender="K", sender_email="k@v.be",
                  subject="Vraag", category_id=cat.id)
    db.session.add(msg)
    db.session.commit()

    pushed: list[str] = []

    class _P:
        def is_configured(self):
            return True

        def apply_label(self, pid, name, color=None):
            pushed.append(name)

    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: _P())

    write_label(msg)
    assert pushed == ["Te beantwoorden"]

    org.outlook_category_numbering = True
    db.session.commit()
    write_label(msg)
    assert pushed[-1] == "1. Te beantwoorden"


# ── Schakelaar ───────────────────────────────────────────────────────────────
def _login(client, email="owner@acme-num.be"):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return client


def test_toggle_flips_the_setting(app, client, org):
    c = _login(client)
    resp = c.post("/api/admin/outlook-numbering/toggle")
    assert resp.status_code == 200 and resp.get_json()["on"] is True
    db.session.refresh(org)
    assert org.outlook_category_numbering is True

    assert c.post("/api/admin/outlook-numbering/toggle").get_json()["on"] is False


def test_toggle_cleans_up_the_unused_names(app, client, org, monkeypatch):
    """Anders houd je zowel 'FYI' als '1. FYI' in je Outlook-categorielijst over."""
    from app.models import MailAccount, User

    owner = db.session.get(User, org.owner_user_id)
    db.session.add(MailAccount(user_id=owner.id, organization_id=org.id, provider="outlook",
                               email="owner@acme-num.be", status="connected"))
    _cat(org, "Te beantwoorden", 0)
    db.session.commit()

    removed: list[str] = []

    class _P:
        def is_configured(self):
            return True

        def remove_master_category(self, name):
            removed.append(name)

    monkeypatch.setattr("app.providers.get_provider", lambda a: _P())
    _login(client).post("/api/admin/outlook-numbering/toggle")   # aan → kale vorm weg
    assert "Te beantwoorden" in removed


def test_toggle_requires_the_permission(app, client, org):
    from app.models import User

    # Het recht intrekken op de bestaande rol; hernoemen zou botsen met de rol die er al is.
    owner = db.session.get(User, org.owner_user_id)
    for rp in owner.role.perms:
        if rp.permission.key == "manageOrgCats":
            rp.allowed = False
    db.session.commit()
    assert _login(client).post("/api/admin/outlook-numbering/toggle").status_code == 403


def test_screen_shows_the_toggle(app, client, org):
    html = _login(client).get("/admin/orgcats").get_data(as_text=True)
    assert "Nummers voor categorieën in Outlook" in html


def test_demo_org_is_unaffected_by_default(app):
    from app.models import Organization

    demo = db.session.query(Organization).filter_by(is_demo=True).first()
    if demo is not None:
        assert bool(demo.outlook_category_numbering) is False

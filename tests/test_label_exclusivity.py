"""Maximaal één Otto-categorie per bericht + her-taggen bij naamswijzigingen.

De bug: apply_label voegde alleen maar samen, dus hercategoriseren of de
nummering omzetten liet het oude Otto-label naast het nieuwe staan — mail droeg
twee (of meer) categorieën, en na een hernoeming bleef de oude naam (grijs, want
uit de categorielijst) op bestaande mail hangen.

Bewaakte beloftes:
1. apply_label vervangt ANDERE Otto-labels (elke nummer-variant) en laat de eigen
   tags van de gebruiker staan;
2. bestaande mail verhuist mee bij een hernoeming of nummering-switch (retag);
3. de opruimknop herschrijft verouderde vormen naar precies één juiste naam.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import Category, MailAccount, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _fake_outlook(monkeypatch, current_categories):
    """OutlookProvider met gemockte Graph-calls; geeft de gedane patches terug."""
    from app.providers.outlook import OutlookProvider

    acct = MailAccount(provider="outlook", email="x@y.be")
    p = OutlookProvider(acct)
    patches: list[dict] = []
    p._ensure_master_category = lambda name, color=None: None
    p._get = lambda path: {"categories": list(current_categories)}
    p._patch = lambda path, payload, extra_headers=None: patches.append(payload) or {}
    return p, patches


# ── 1. apply_label: vervangen, niet stapelen ─────────────────────────────────
def test_apply_label_replaces_other_otto_variants(app, monkeypatch):
    p, patches = _fake_outlook(monkeypatch, ["Te beantwoorden", "3. Meldingen", "Privé-tag"])
    p.apply_label("M1", "1. Te beantwoorden", replace_bases={"Te beantwoorden", "Meldingen"})
    assert patches == [{"categories": ["Privé-tag", "1. Te beantwoorden"]}]


def test_apply_label_keeps_user_tags_and_skips_noop(app, monkeypatch):
    p, patches = _fake_outlook(monkeypatch, ["Privé-tag", "1. Te beantwoorden"])
    p.apply_label("M1", "1. Te beantwoorden", replace_bases={"Te beantwoorden"})
    assert patches == []  # al correct → geen overbodige Graph-call


def test_apply_label_without_bases_still_merges(app, monkeypatch):
    p, patches = _fake_outlook(monkeypatch, ["Privé-tag"])
    p.apply_label("M1", "FYI")
    assert patches == [{"categories": ["Privé-tag", "FYI"]}]


def test_write_label_passes_the_org_base_names(app, monkeypatch):
    from app.tasks import writeback

    org_user = db.session.query(User).filter_by(email="demo@example.com").first()
    cat = db.session.query(Category).filter_by(organization_id=org_user.organization_id).first()
    acct = MailAccount(user_id=org_user.id, organization_id=org_user.organization_id,
                       provider="outlook", email="d@x.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    msg = type("M", (), {})()
    msg.account = acct
    msg.provider_message_id = "PM1"
    msg.category = cat

    seen = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def apply_label(self, pmid, name, color=None, replace_bases=None):
            seen.update(name=name, replace_bases=replace_bases)

    monkeypatch.setattr(writeback, "get_provider", lambda a: FakeProvider())
    writeback.write_label(msg)
    from app.outlook_labels import strip_number

    assert strip_number(cat.name) in seen["replace_bases"]


def test_write_label_falls_back_for_old_provider_signature(app, monkeypatch):
    from app.tasks import writeback

    org_user = db.session.query(User).filter_by(email="demo@example.com").first()
    cat = db.session.query(Category).filter_by(organization_id=org_user.organization_id).first()
    acct = MailAccount(user_id=org_user.id, provider="outlook", email="d2@x.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    msg = type("M", (), {})()
    msg.account = acct
    msg.provider_message_id = "PM1"
    msg.category = cat

    calls = []

    class LegacyProvider:
        def is_configured(self):
            return True

        def apply_label(self, pmid, name, color=None):  # geen replace_bases
            calls.append(name)

    monkeypatch.setattr(writeback, "get_provider", lambda a: LegacyProvider())
    writeback.write_label(msg)
    assert len(calls) == 1


# ── 2. retag: bestaande mail verhuist mee ────────────────────────────────────
def test_retag_messages_rewrites_and_dedupes(app, monkeypatch):
    from app.providers.outlook import OutlookProvider

    acct = MailAccount(provider="outlook", email="x@y.be")
    p = OutlookProvider(acct)
    patches, removed = [], []
    p._ensure_master_category = lambda name, color=None: None
    p.remove_master_category = lambda name: removed.append(name)
    p._get_abs = lambda path: {"value": [
        {"id": "A", "categories": ["Te beantwoorden", "1. Te beantwoorden", "Privé"]},
        {"id": "B", "categories": ["Te beantwoorden"]},
    ]}
    p._patch = lambda path, payload, extra_headers=None: patches.append((path.split("/")[-1], payload)) or {}

    done = p.retag_messages("Te beantwoorden", "1. Te beantwoorden",
                            replace_bases={"Te beantwoorden"})
    assert done == 2
    # Dubbele tags gededupliceerd; eigen tag blijft; oude naam uit de lijst.
    assert patches == [
        ("A", {"categories": ["Privé", "1. Te beantwoorden"]}),
        ("B", {"categories": ["1. Te beantwoorden"]}),
    ]
    assert removed == ["Te beantwoorden"]


def test_stale_variants_maps_every_form_to_the_current_label(app):
    org_user = db.session.query(User).filter_by(email="demo@example.com").first()
    org = org_user.organization
    org.outlook_category_numbering = True
    db.session.commit()
    from app.outlook_labels import label_for, stale_variants

    cats = sorted(org.categories, key=lambda c: c.position or 0)
    mapping = stale_variants(org)
    first = cats[0]
    assert mapping[first.name][0] == label_for(first, org)          # ongenummerd → juist
    assert f"{len(cats)}. {first.name}" in mapping                   # oud nummer → juist
    assert label_for(first, org) not in mapping                     # juiste vorm zelf niet


# ── 3. hernoemen + opruimknop ────────────────────────────────────────────────
def test_renaming_a_category_retags_existing_mail(app, auth, monkeypatch):
    org_user = db.session.query(User).filter_by(email="demo@example.com").first()
    cat = db.session.query(Category).filter_by(organization_id=org_user.organization_id).first()
    calls = []
    import app.tasks.retag as retag

    monkeypatch.setattr(retag, "retag_org_labels", lambda org, mapping: calls.append(mapping))
    old_name = cat.name
    r = auth.put(f"/api/categories/{cat.id}", json={"name": "Compleet nieuw", "color": cat.color})
    assert r.status_code == 200
    assert len(calls) == 1
    (old_label, (new_label, _color)), = calls[0].items()
    assert old_name in old_label and "Compleet nieuw" in new_label


def test_realign_endpoint_requires_perm_and_enqueues(app, auth, monkeypatch):
    calls = []
    import app.tasks.retag as retag

    monkeypatch.setattr(retag, "retag_org_labels", lambda org, mapping: calls.append(mapping))
    r = auth.post("/api/admin/outlook-labels/realign")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert len(calls) == 1

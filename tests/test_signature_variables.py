"""Eigen handtekening-variabelen + M365-profielverversing + Regels zonder Graph-id's.

Bewaakte beloftes:
1. eigen variabelen (org-breed door de admin, persoonlijk door de gebruiker) vullen
   {{key}}-placeholders — de admin vult voor iedereen, de gebruiker alleen zichzelf,
   en andermans persoonlijke variabelen blijven onzichtbaar;
2. de M365-profielvelden worden ±dagelijks bijgevuld tijdens de sync (alleen lege
   velden — eigen invoer wint);
3. het Regels-scherm toont nooit meer een rauw Graph-map-id.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import MailAccount, SignatureVariable, SignatureVariableValue, User
from app.seed.demo_data import DEMO_PASSWORD
from app.signatures import placeholder_values, render_placeholders, slugify_key


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _second_user(org_id):
    return (db.session.query(User)
            .filter(User.organization_id == org_id, User.email != "demo@example.com")
            .first())


def _org_var(org_id, key="linkedin", label="LinkedIn-URL", user_id=None):
    var = SignatureVariable(organization_id=org_id, user_id=user_id, key=key, label=label)
    db.session.add(var)
    db.session.commit()
    return var


# ── slugify + rendering ──────────────────────────────────────────────────────
def test_slugify_key_normalises_and_guards():
    assert slugify_key("LinkedIn-URL") == "linkedin_url"
    assert slugify_key("Lijn 2") == "lijn_2"
    assert slugify_key("naam") is None       # botst met een vaste placeholder
    assert slugify_key("2e lijn") is None    # moet met een letter beginnen
    assert slugify_key("  ") is None


def test_variable_values_fill_placeholders(app):
    user = _demo_user()
    var = _org_var(user.organization_id, key="lijn_2", label="Lijn 2")
    db.session.add(SignatureVariableValue(organization_id=user.organization_id,
                                          variable_id=var.id, user_id=user.id, value="Kantoor 12"))
    db.session.commit()
    assert placeholder_values(user)["lijn_2"] == "Kantoor 12"
    assert render_placeholders("A {{lijn_2}} B", user) == "A Kantoor 12 B"


def test_personal_variable_is_invisible_to_others(app):
    user = _demo_user()
    other = _second_user(user.organization_id)
    _org_var(user.organization_id, key="prive", label="Privé", user_id=other.id)
    assert "prive" not in placeholder_values(user)
    assert "prive" in placeholder_values(other)


def test_fixed_fields_always_win_over_a_variable(app):
    user = _demo_user()
    user.job_title = "Echte functie"
    _org_var(user.organization_id, key="functie", label="Nep")  # kan via slugify niet, maar direct wel
    db.session.commit()
    assert placeholder_values(user)["functie"] == "Echte functie"


def test_personal_signature_renders_placeholders(app):
    from app.models import StyleProfile
    from app.signatures import resolve

    user = _demo_user()
    var = _org_var(user.organization_id, key="kenmerk", label="Kenmerk", user_id=user.id)
    db.session.add(SignatureVariableValue(organization_id=user.organization_id,
                                          variable_id=var.id, user_id=user.id, value="X-99"))
    style = user.style_profile or StyleProfile(user_id=user.id)
    style.signature = "Groeten — kenmerk {{kenmerk}}"
    db.session.add(style)
    db.session.commit()
    assert resolve(user) == "Groeten — kenmerk X-99"


# ── admin: definiëren + voor iedereen invullen ──────────────────────────────
def test_admin_adds_variable_and_fills_values_for_everyone(app, auth):
    user = _demo_user()
    other = _second_user(user.organization_id)
    r = auth.post("/admin/signatures/vars/add", data={"label": "Aansluitnummer"},
                  follow_redirects=True)
    assert r.status_code == 200
    var = db.session.query(SignatureVariable).filter_by(
        organization_id=user.organization_id, key="aansluitnummer").one()
    assert var.user_id is None  # org-breed

    auth.post("/admin/signatures/vars/values", data={
        f"val-{var.id}-{user.id}": "A-1", f"val-{var.id}-{other.id}": "A-2",
    })
    assert placeholder_values(user)["aansluitnummer"] == "A-1"
    assert placeholder_values(other)["aansluitnummer"] == "A-2"

    auth.post(f"/admin/signatures/vars/{var.id}/delete")
    assert db.session.query(SignatureVariable).filter_by(key="aansluitnummer").count() == 0


def test_admin_cannot_delete_a_personal_variable(app, auth):
    user = _demo_user()
    other = _second_user(user.organization_id)
    var = _org_var(user.organization_id, key="van_ander", label="Van ander", user_id=other.id)
    assert auth.post(f"/admin/signatures/vars/{var.id}/delete").status_code == 404


# ── gebruiker: alleen zichzelf ───────────────────────────────────────────────
def test_user_creates_personal_variable_and_fills_own_value(app, auth):
    user = _demo_user()
    org_var = _org_var(user.organization_id, key="klantcode", label="Klantcode")
    r = auth.post("/app/settings/profiel", data={
        "job_title": "Zaakvoerder",
        f"var-{org_var.id}": "KC-7",
        "new_var_label": "Mijn motto", "new_var_value": "Doe maar gewoon",
    }, follow_redirects=True)
    assert r.status_code == 200
    values = placeholder_values(user)
    assert values["klantcode"] == "KC-7"
    assert values["mijn_motto"] == "Doe maar gewoon"
    personal = db.session.query(SignatureVariable).filter_by(key="mijn_motto").one()
    assert personal.user_id == user.id

    # Verwijderen kan alleen voor eigen persoonlijke variabelen.
    assert auth.post(f"/app/settings/variabele/{personal.id}/delete",
                     follow_redirects=True).status_code == 200
    assert db.session.query(SignatureVariable).filter_by(key="mijn_motto").count() == 0
    assert auth.post(f"/app/settings/variabele/{org_var.id}/delete").status_code == 404


# ── M365-profielverversing tijdens de sync ───────────────────────────────────
def test_sync_refreshes_profile_blanks_from_graph(app):
    from app.tasks.sync import _maybe_refresh_groups

    user = _demo_user()
    user.job_title = None
    user.department = None
    user.phone = "+32 9 111 11 11"  # zelf ingevuld → mag NIET overschreven worden
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="d@x.be", status="connected")
    db.session.add(acct)
    db.session.commit()

    class FakeProvider:
        def my_group_ids(self):
            return ["g-1"]

        def get_profile(self):
            return {"jobTitle": "CTO", "department": "Engineering",
                    "businessPhones": ["+32 9 999 99 99"], "mobilePhone": "+32 499 00 00 00"}

    _maybe_refresh_groups(acct, FakeProvider())
    db.session.refresh(user)
    assert user.job_title == "CTO" and user.department == "Engineering"
    assert user.phone == "+32 9 111 11 11"  # eigen invoer bleef staan
    assert acct.graph_group_ids == ["g-1"] and acct.graph_groups_synced_at is not None


# ── Regels: nooit een Graph-id op het scherm ─────────────────────────────────
def test_describe_never_shows_a_graph_folder_id(app):
    from app.mailrules import describe, looks_like_graph_id

    gid = "AAMkADdmZTk0M2IxLTY3NzgtNDgzMS1iZjA1AC0xMmU3YmZhQA6ZDdjAAuAAAAAAA="
    assert looks_like_graph_id(gid)
    assert not looks_like_graph_id("Klanten / Facturen")

    rule = type("R", (), {})()
    rule.conditions = {"from_addresses": ["a@b.be"]}
    rule.actions = {"move_to_folder": gid}
    _conds, acts = describe(rule)
    assert acts == ["Verplaats naar map: een map in Outlook"]
    assert gid not in " ".join(acts)


def test_to_graph_uses_an_id_stored_as_name(app):
    from app.mailrules import to_graph

    gid = "AAMkADdmZTk0M2IxLTY3NzgtNDgzMS1iZjA1AC0xMmU3YmZhQA6ZDdjAAuAAAAAAA="
    rule = type("R", (), {})()
    rule.name = "Test"
    rule.enabled = True
    rule.position = 1
    rule.sequence = 1
    rule.conditions = {"from_addresses": ["a@b.be"]}
    rule.actions = {"move_to_folder": gid}
    payload = to_graph(rule, folder_ids={})
    assert payload["actions"]["moveToFolder"] == gid

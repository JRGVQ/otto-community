"""Inbox-regels: Otto beheert ze, Outlook voert ze uit.

De nadruk ligt op de vertaling naar en van Graph, op de validatie die onzin tegenhoudt,
en op de grens: niemand mag regels zetten op een mailbox waar hij niet bij hoort.
"""
from __future__ import annotations

import pytest

from app import mailrules as svc
from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, MailboxMember, MailRule, Role, User
from app.models.user import initials_for


@pytest.fixture()
def owner(app):
    u = create_company("Acme BV", "Owner", "owner@acme-rul.be", "geheim123")
    u.email_verified = True
    u.onboarded = True
    db.session.commit()
    return u


def _account(owner, email="owner@acme-rul.be", shared=False, members=()):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected",
                       mailbox_type="shared" if shared else "personal")
    db.session.add(acct)
    db.session.flush()
    if shared:
        for u in (owner, *members):
            db.session.add(MailboxMember(mail_account_id=acct.id, user_id=u.id, role="agent"))
    db.session.commit()
    return acct


def _member(org, email="collega@acme-rul.be"):
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    u = User(email=email, name="Collega", initials=initials_for("Collega"), avatar_color="#111",
             organization_id=org.id, role_id=role.id if role else None, status="Actief",
             email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()
    return u


def _rule(acct, **kw):
    r = MailRule(organization_id=acct.organization_id, account_id=acct.id,
                 name=kw.pop("name", "Facturen"), sequence=kw.pop("sequence", 1),
                 conditions=kw.pop("conditions", {"subject_contains": ["factuur"]}),
                 actions=kw.pop("actions", {"move_to_folder": "Boekhouding"}), **kw)
    db.session.add(r)
    db.session.commit()
    return r


class _Provider:
    supports_rules = True

    def __init__(self, folders=("Boekhouding",), rules=None, fail=False):
        self._folders = list(folders)
        self._rules = list(rules or [])
        self.created: list[dict] = []
        self.updated: list[tuple] = []
        self.deleted: list[str] = []
        self.ensured: list[str] = []
        self._fail = fail

    def is_configured(self):
        return True

    def list_folders(self):
        return [{"id": f"id-{n}", "name": n} for n in self._folders]

    def ensure_folder(self, name):
        self.ensured.append(name)
        self._folders.append(name)
        return f"id-{name}"

    def list_rules(self):
        return list(self._rules)

    def create_rule(self, payload):
        if self._fail:
            raise RuntimeError("Outlook weigerde de regel")
        self.created.append(payload)
        return f"outlook-{len(self.created)}"

    def update_rule(self, rule_id, payload):
        if self._fail:
            raise RuntimeError("Outlook weigerde de wijziging")
        self.updated.append((rule_id, payload))

    def delete_rule(self, rule_id):
        self.deleted.append(rule_id)


# ── Validatie ────────────────────────────────────────────────────────────────
def test_a_rule_needs_a_condition(app):
    """Zonder voorwaarde zou de regel op álle mail slaan."""
    with pytest.raises(svc.RuleError, match="voorwaarde"):
        svc.validate("Naam", {}, {"mark_as_read": True})


def test_a_rule_needs_a_name_and_an_action(app):
    with pytest.raises(svc.RuleError, match="naam"):
        svc.validate("", {"sent_to_me": True}, {"mark_as_read": True})
    with pytest.raises(svc.RuleError, match="gebeuren"):
        svc.validate("Naam", {"sent_to_me": True}, {})


def test_deleting_and_moving_together_is_refused(app):
    with pytest.raises(svc.RuleError, match="Verwijderen"):
        svc.validate("Naam", {"sent_to_me": True},
                     {"delete": True, "move_to_folder": "Archief"})


def test_unknown_keys_are_dropped(app):
    """Graph weigert een regel met een onbekend veld in zijn geheel."""
    c, a = svc.normalise({"verzonnen": ["x"], "sent_to_me": True}, {"onzin": 1, "mark_as_read": True})
    assert c == {"sent_to_me": True} and a == {"mark_as_read": True}


def test_empty_values_are_dropped(app):
    c, a = svc.normalise({"subject_contains": ["", "  "]}, {"assign_categories": []})
    assert c == {} and a == {}


def test_a_comma_separated_string_becomes_a_list(app):
    c, _ = svc.normalise({"subject_contains": "factuur, rekening"}, {})
    assert c["subject_contains"] == ["factuur", "rekening"]


# ── Vertaling ────────────────────────────────────────────────────────────────
def test_conditions_and_actions_become_graph(app, owner):
    acct = _account(owner)
    rule = _rule(acct, conditions={"subject_contains": ["factuur"],
                                   "from_addresses": ["boekhouding@leverancier.be"],
                                   "has_attachments": True},
                 actions={"move_to_folder": "Boekhouding", "mark_as_read": True})
    payload = svc.to_graph(rule, {"Boekhouding": "id-Boekhouding"})

    assert payload["conditions"]["subjectContains"] == ["factuur"]
    assert payload["conditions"]["fromAddresses"][0]["emailAddress"]["address"] == "boekhouding@leverancier.be"
    assert payload["conditions"]["hasAttachments"] is True
    assert payload["actions"]["moveToFolder"] == "id-Boekhouding"
    assert payload["actions"]["markAsRead"] is True
    assert payload["actions"]["stopProcessingRules"] is False  # altijd expliciet


def test_a_missing_folder_drops_only_that_action(app, owner):
    """De rest van de regel blijft bruikbaar in plaats van helemaal te mislukken."""
    acct = _account(owner)
    rule = _rule(acct, actions={"move_to_folder": "Bestaat niet", "mark_as_read": True})
    payload = svc.to_graph(rule, {})
    assert "moveToFolder" not in payload["actions"]
    assert payload["actions"]["markAsRead"] is True


def test_graph_rules_can_be_read_back(app):
    raw = {
        "conditions": {"subjectContains": ["factuur"], "hasAttachments": True,
                       "fromAddresses": [{"emailAddress": {"address": "a@b.be"}}]},
        "actions": {"moveToFolder": "id-9", "markAsRead": True, "stopProcessingRules": True},
    }
    c, a = svc.from_graph(raw, {"id-9": "Boekhouding"})
    assert c["subject_contains"] == ["factuur"] and c["has_attachments"] is True
    assert c["from_addresses"] == ["a@b.be"]
    assert a["move_to_folder"] == "Boekhouding" and a["stop_processing"] is True


def test_round_trip_keeps_the_meaning(app, owner):
    acct = _account(owner)
    rule = _rule(acct, conditions={"subject_contains": ["factuur"], "sent_to_me": True},
                 actions={"move_to_folder": "Boekhouding", "mark_as_read": True})
    payload = svc.to_graph(rule, {"Boekhouding": "id-B"})
    c, a = svc.from_graph(payload, {"id-B": "Boekhouding"})
    assert c == rule.conditions
    assert a == rule.actions


# ── Pushen ───────────────────────────────────────────────────────────────────
def test_push_creates_the_rule_in_outlook(app, owner):
    acct = _account(owner)
    rule = _rule(acct)
    p = _Provider()
    assert svc.push(rule, p) is True
    assert rule.provider_rule_id == "outlook-1" and rule.last_error is None
    assert p.created[0]["displayName"] == "Facturen"


def test_push_updates_an_existing_rule(app, owner):
    acct = _account(owner)
    rule = _rule(acct, provider_rule_id="outlook-7")
    p = _Provider()
    svc.push(rule, p)
    assert p.updated and p.updated[0][0] == "outlook-7"
    assert not p.created


def test_a_missing_folder_is_created(app, owner):
    """Anders zou de verplaatsactie stil wegvallen."""
    acct = _account(owner)
    rule = _rule(acct, actions={"move_to_folder": "Nieuwe map"})
    p = _Provider(folders=[])
    svc.push(rule, p)
    assert "Nieuwe map" in p.ensured


def test_a_failed_push_is_recorded_on_the_rule(app, owner):
    """Zichtbaar in het scherm in plaats van stil weggelogd."""
    acct = _account(owner)
    rule = _rule(acct)
    assert svc.push(rule, _Provider(fail=True)) is False
    assert rule.last_error and rule.in_outlook is False


def test_push_refuses_a_provider_without_rules(app, owner):
    class _NoRules(_Provider):
        supports_rules = False

    rule = _rule(_account(owner))
    assert svc.push(rule, _NoRules()) is False
    assert "ondersteunt geen" in rule.last_error


# ── Inlezen ──────────────────────────────────────────────────────────────────
def test_existing_outlook_rules_are_imported(app, owner):
    acct = _account(owner)
    p = _Provider(rules=[{
        "id": "outlook-1", "displayName": "Nieuwsbrieven", "sequence": 2, "isEnabled": True,
        "conditions": {"subjectContains": ["nieuwsbrief"]},
        "actions": {"moveToFolder": "id-Boekhouding"},
    }])
    assert svc.pull(acct, p) == 1
    rule = db.session.query(MailRule).filter_by(account_id=acct.id).one()
    assert rule.name == "Nieuwsbrieven" and rule.provider_rule_id == "outlook-1"
    assert rule.actions["move_to_folder"] == "Boekhouding"


def test_importing_twice_does_not_duplicate(app, owner):
    acct = _account(owner)
    p = _Provider(rules=[{"id": "outlook-1", "displayName": "A", "isEnabled": True,
                          "conditions": {"subjectContains": ["x"]}, "actions": {}}])
    assert svc.pull(acct, p) == 1
    assert svc.pull(acct, p) == 0
    assert db.session.query(MailRule).filter_by(account_id=acct.id).count() == 1


# ── Verwijderen ──────────────────────────────────────────────────────────────
def test_remove_deletes_in_outlook_and_locally(app, owner):
    acct = _account(owner)
    rule = _rule(acct, provider_rule_id="outlook-3")
    p = _Provider()
    svc.remove(rule, p)
    assert p.deleted == ["outlook-3"]
    assert db.session.query(MailRule).count() == 0


def test_deep_delete_purges_rules(app, owner):
    from app.tenancy import delete_org_deep

    oid = owner.organization_id
    _rule(_account(owner))
    delete_org_deep(owner.organization)
    db.session.commit()
    assert db.session.query(MailRule).filter_by(organization_id=oid).count() == 0


# ── API + grenzen ────────────────────────────────────────────────────────────
def _login(client, email="owner@acme-rul.be"):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return client


def test_api_creates_a_rule(app, client, owner, monkeypatch):
    acct = _account(owner)
    monkeypatch.setattr("app.providers.get_provider", lambda a: _Provider())
    resp = _login(client).post(f"/api/rules/{acct.id}", json={
        "name": "Facturen",
        "conditions": {"subject_contains": "factuur"},
        "actions": {"move_to_folder": "Boekhouding"},
    })
    assert resp.status_code == 200
    assert db.session.query(MailRule).filter_by(account_id=acct.id).count() == 1


def test_api_refuses_an_invalid_rule(app, client, owner):
    acct = _account(owner)
    resp = _login(client).post(f"/api/rules/{acct.id}", json={"name": "Leeg", "conditions": {}, "actions": {}})
    assert resp.status_code == 400
    assert "voorwaarde" in resp.get_json()["error"]


def test_api_refuses_a_mailbox_you_are_not_in(app, client, owner):
    outsider = _member(owner.organization)
    acct = _account(owner, "support@acme-rul.be", shared=True)  # outsider is geen lid
    resp = _login(client, outsider.email).post(f"/api/rules/{acct.id}", json={
        "name": "X", "conditions": {"sent_to_me": True}, "actions": {"mark_as_read": True}})
    assert resp.status_code == 403


def test_api_refuses_another_organisation(app, client, owner):
    other = create_company("Rivaal NV", "R", "r@rivaal-rul.be", "geheim123")
    other.email_verified = True
    other.onboarded = True
    db.session.commit()
    rule = _rule(_account(owner))
    assert _login(client, other.email).post(f"/api/rules/rule/{rule.id}/delete").status_code == 404


def test_api_reorders_rules(app, client, owner, monkeypatch):
    acct = _account(owner)
    first = _rule(acct, name="Eerste", sequence=1)
    second = _rule(acct, name="Tweede", sequence=2)
    monkeypatch.setattr("app.providers.get_provider", lambda a: _Provider())

    _login(client).post(f"/api/rules/rule/{second.id}/move", json={"direction": "up"})
    db.session.refresh(first), db.session.refresh(second)
    assert second.sequence < first.sequence


def test_api_toggles_a_rule(app, client, owner, monkeypatch):
    rule = _rule(_account(owner))
    monkeypatch.setattr("app.providers.get_provider", lambda a: _Provider())
    resp = _login(client).post(f"/api/rules/rule/{rule.id}/toggle")
    assert resp.get_json()["on"] is False


def test_screen_renders(app, client, owner, monkeypatch):
    _account(owner)
    monkeypatch.setattr("app.providers.get_provider", lambda a: _Provider())
    html = _login(client).get("/app/rules").get_data(as_text=True)
    assert "Nieuwe regel" in html and "Onderwerp bevat" in html

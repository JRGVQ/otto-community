"""Klantmail loggen in de eigen Odoo van een organisatie.

De grens die telt: alleen loggen bij een contact dat al bestaat. Wie mailt en niet in Odoo
staat, is geen klant — daar een contact voor aanmaken zou het CRM vervuilen. En het mag de
mailverwerking nooit breken: een onbereikbare Odoo geeft "niets gelogd", geen crash.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def org(app):
    from app.auth.service import create_company
    from app.extensions import db

    user = create_company("Acme BV", "Owner", "owner@acme-crm.be", "geheim123")
    db.session.commit()
    return user.organization


@pytest.fixture()
def connection(app, org):
    from app.extensions import db
    from app.models import CrmConnection

    conn = CrmConnection(
        organization_id=org.id, provider="odoo", base_url="https://acme.odoo.com",
        db_name="acme", username="bot@acme.be", enabled=True, log_incoming=True,
    )
    conn.api_key = "geheim-sleutel"
    db.session.add(conn)
    db.session.commit()
    return conn


class _FakeClient:
    """Bootst Odoo na: één bekend contact met (optioneel) een open lead."""

    def __init__(self, conn, known_email="jan@klant.be", lead=True):
        self.known_email = known_email
        self.lead = lead
        self.posts = []   # (model, id, subject)

    def exec(self, model, method, *args, **kw):
        if model == "res.partner" and method == "search_read":
            domain = args[0]
            email = domain[0][2]
            return [{"id": 7, "name": "Jan Klant"}] if email == self.known_email else []
        if model == "crm.lead" and method == "search_read":
            return [{"id": 55}] if self.lead else []
        if method == "message_post":
            self.posts.append((model, args[0], kw.get("subject")))
            return 100
        raise AssertionError(f"onverwachte call {model}.{method}")


def test_the_api_key_is_stored_encrypted(app, connection):
    from app.extensions import db
    from app.models import CrmConnection

    row = db.session.get(CrmConnection, connection.id)
    assert row.api_key == "geheim-sleutel"          # ontsleutelt correct
    assert row.api_key_enc != "geheim-sleutel"      # maar staat versleuteld in de kolom
    assert row.is_ready is True


def test_a_known_contact_gets_the_mail_logged_and_the_open_deal_too(app, connection, monkeypatch):
    from app.crm import odoo_client as tenant

    fake = _FakeClient(connection)
    monkeypatch.setattr(tenant, "TenantOdoo", lambda conn: fake)

    result = tenant.log_mail(connection, "jan@klant.be", "Vraag over offerte", "Body hier")
    assert result["logged"] is True and result["partner_id"] == 7 and result["lead_id"] == 55
    # Zowel het contact als de open deal krijgen de notitie.
    assert ("res.partner", [7], "Vraag over offerte") in fake.posts
    assert ("crm.lead", [55], "Vraag over offerte") in fake.posts


def test_a_contact_without_an_open_deal_is_still_logged(app, connection, monkeypatch):
    from app.crm import odoo_client as tenant

    fake = _FakeClient(connection, lead=False)
    monkeypatch.setattr(tenant, "TenantOdoo", lambda conn: fake)

    result = tenant.log_mail(connection, "jan@klant.be", "Hallo", "")
    assert result["logged"] is True and result["lead_id"] is None
    assert [p[0] for p in fake.posts] == ["res.partner"]


def test_an_unknown_sender_is_left_alone(app, connection, monkeypatch):
    from app.crm import odoo_client as tenant

    fake = _FakeClient(connection)
    monkeypatch.setattr(tenant, "TenantOdoo", lambda conn: fake)

    result = tenant.log_mail(connection, "vreemde@ergens.be", "Spam?", "…")
    assert result["logged"] is False and result["reason"] == "no_partner"
    assert fake.posts == []


def test_a_broken_odoo_never_raises(app, connection, monkeypatch):
    from app.crm import odoo_client as tenant

    def boom(conn):
        raise RuntimeError("Odoo plat")

    monkeypatch.setattr(tenant, "TenantOdoo", boom)
    assert tenant.log_mail(connection, "jan@klant.be", "x", "y")["logged"] is False


def test_a_disabled_connection_logs_nothing(app, connection, monkeypatch):
    from app.extensions import db
    from app.crm import odoo_client as tenant

    connection.enabled = False
    db.session.commit()
    called = {"n": 0}
    monkeypatch.setattr(tenant, "TenantOdoo", lambda conn: called.__setitem__("n", called["n"] + 1))
    assert tenant.log_mail(connection, "jan@klant.be", "x", "y")["logged"] is False
    assert called["n"] == 0   # een uitgeschakelde koppeling wordt niet eens opgebeld


# ── De brug vanuit de pijplijn ───────────────────────────────────────────────
class _Msg:
    def __init__(self, org_id, sender="jan@klant.be"):
        self.organization_id = org_id
        self.sender_email = sender
        self.subject = "Vraag"
        self.body = "Body"
        self.id = 1


def test_the_pipeline_hook_logs_when_a_connection_is_ready(app, org, connection, monkeypatch):
    from app.crm import odoo_client as tenant
    from app.tasks.crm_log import log_customer_mail

    fake = _FakeClient(connection)
    monkeypatch.setattr(tenant, "TenantOdoo", lambda conn: fake)
    assert log_customer_mail(_Msg(org.id)) is True


def test_the_pipeline_hook_is_a_noop_without_a_connection(app, org):
    from app.tasks.crm_log import log_customer_mail

    # Andere organisatie zonder koppeling → niets.
    assert log_customer_mail(_Msg(org.id + 999)) is False

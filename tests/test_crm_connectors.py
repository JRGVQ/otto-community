"""De standaard-CRM-koppelingen: HubSpot, Pipedrive en de generieke webhook.

Zelfde grondregels als de Odoo-koppeling (tests/test_crm_log.py): alleen loggen bij
een contact dat al bestaat (behalve de webhook, die alles doorstuurt en het filteren
aan de ontvanger laat), en een kapot CRM mag de mailverwerking nooit breken. De
HTTP-laag wordt aan de modulegrens vervangen — geen echt netwerk in de tests.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture()
def org(app):
    from app.auth.service import create_company
    from app.extensions import db

    user = create_company("Beta BV", "Owner", "owner@beta-crm.be", "geheim123")
    db.session.commit()
    return user.organization


def _connection(org, provider, **kw):
    from app.extensions import db
    from app.models import CrmConnection

    conn = CrmConnection(organization_id=org.id, provider=provider,
                         enabled=True, log_incoming=True, **kw)
    db.session.add(conn)
    db.session.commit()
    return conn


# ── HubSpot ──────────────────────────────────────────────────────────────────
class _FakeHubSpot:
    """Bootst de HubSpot-API na: één bekend contact, en onthoudt elke call."""

    def __init__(self, known_email="jan@klant.be"):
        self.known_email = known_email
        self.calls = []   # (method, url, payload)

    def __call__(self, method, url, payload=None, headers=None):
        self.calls.append((method, url, payload))
        assert headers and headers["Authorization"].startswith("Bearer ")
        if url.endswith("/contacts/search"):
            email = payload["filterGroups"][0]["filters"][0]["value"]
            return {"results": [{"id": "301"}]} if email == self.known_email else {"results": []}
        if url.endswith("/notes"):
            return {"id": "900"}
        if "/contacts?limit=1" in url:
            return {"results": []}
        raise AssertionError(f"onverwachte call {url}")


@pytest.fixture()
def hubspot_conn(app, org):
    conn = _connection(org, "hubspot")
    conn.api_key = "pat-eu1-test"
    from app.extensions import db
    db.session.commit()
    return conn


def test_hubspot_logs_a_note_on_the_existing_contact(app, hubspot_conn, monkeypatch):
    from app.crm import hubspot

    fake = _FakeHubSpot()
    monkeypatch.setattr(hubspot, "http_json", fake)
    connector = hubspot.HubSpotConnector(hubspot_conn)

    result = connector.log_mail("jan@klant.be", "Vraag over offerte", "Body hier")
    assert result["logged"] is True and result["contact_id"] == "301"
    note_call = next(c for c in fake.calls if c[1].endswith("/notes"))
    assert "Vraag over offerte" in note_call[2]["properties"]["hs_note_body"]
    assert note_call[2]["associations"][0]["to"]["id"] == "301"


def test_hubspot_leaves_unknown_senders_alone(app, hubspot_conn, monkeypatch):
    from app.crm import hubspot

    fake = _FakeHubSpot()
    monkeypatch.setattr(hubspot, "http_json", fake)
    result = hubspot.HubSpotConnector(hubspot_conn).log_mail("vreemde@ergens.be", "Spam?", "…")
    assert result == {"logged": False, "reason": "no_contact"}
    assert not any(c[1].endswith("/notes") for c in fake.calls)


def test_a_broken_hubspot_never_raises(app, hubspot_conn, monkeypatch):
    from app.crm import hubspot
    from app.crm.base import CRMError

    def boom(*a, **kw):
        raise CRMError("HTTP 401: token vervallen")

    monkeypatch.setattr(hubspot, "http_json", boom)
    result = hubspot.HubSpotConnector(hubspot_conn).log_mail("jan@klant.be", "x", "y")
    assert result["logged"] is False and result["reason"] == "error"
    # De testknop toont diezelfde fout WEL — daar wil de beheerder ze zien.
    test = hubspot.HubSpotConnector(hubspot_conn).test_connection()
    assert test["ok"] is False and "401" in test["detail"]


# ── Pipedrive ────────────────────────────────────────────────────────────────
class _FakePipedrive:
    def __init__(self, known=True, deal=True):
        self.known = known
        self.deal = deal
        self.calls = []

    def __call__(self, method, url, payload=None, headers=None):
        self.calls.append((method, url, payload))
        assert "api_token=pd-token" in url
        if "/persons/search" in url:
            items = [{"item": {"id": 12}}] if self.known else []
            return {"success": True, "data": {"items": items}}
        if "/deals?" in url:
            return {"success": True, "data": [{"id": 44}] if self.deal else []}
        if "/notes?" in url:
            return {"success": True, "data": {"id": 5}}
        if "/users/me" in url:
            return {"success": True, "data": {"name": "Bot"}}
        raise AssertionError(f"onverwachte call {url}")


@pytest.fixture()
def pipedrive_conn(app, org):
    conn = _connection(org, "pipedrive")
    conn.api_key = "pd-token"
    from app.extensions import db
    db.session.commit()
    return conn


def test_pipedrive_logs_on_the_person_and_the_open_deal(app, pipedrive_conn, monkeypatch):
    from app.crm import pipedrive

    fake = _FakePipedrive()
    monkeypatch.setattr(pipedrive, "http_json", fake)
    result = pipedrive.PipedriveConnector(pipedrive_conn).log_mail("jan@klant.be", "Vraag", "Body")
    assert result["logged"] is True and result["person_id"] == 12 and result["deal_id"] == 44
    note_call = next(c for c in fake.calls if "/notes?" in c[1])
    assert note_call[2]["person_id"] == 12 and note_call[2]["deal_id"] == 44
    assert "Vraag" in note_call[2]["content"]


def test_pipedrive_without_deal_still_logs_on_the_person(app, pipedrive_conn, monkeypatch):
    from app.crm import pipedrive

    fake = _FakePipedrive(deal=False)
    monkeypatch.setattr(pipedrive, "http_json", fake)
    result = pipedrive.PipedriveConnector(pipedrive_conn).log_mail("jan@klant.be", "Hallo", "")
    assert result["logged"] is True and result["deal_id"] is None
    note_call = next(c for c in fake.calls if "/notes?" in c[1])
    assert "deal_id" not in note_call[2]


def test_pipedrive_leaves_unknown_senders_alone(app, pipedrive_conn, monkeypatch):
    from app.crm import pipedrive

    fake = _FakePipedrive(known=False)
    monkeypatch.setattr(pipedrive, "http_json", fake)
    result = pipedrive.PipedriveConnector(pipedrive_conn).log_mail("vreemde@ergens.be", "?", "")
    assert result == {"logged": False, "reason": "no_contact"}


# ── Webhook ──────────────────────────────────────────────────────────────────
@pytest.fixture()
def webhook_conn(app, org):
    conn = _connection(org, "webhook", base_url="https://hooks.klant.be/otto")
    conn.api_key = "webhook-geheim"
    from app.extensions import db
    db.session.commit()
    return conn


def _capture_urlopen(monkeypatch, captured, status=200):
    import urllib.request

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        if status >= 400:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, status, "kapot", {}, None)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_the_webhook_forwards_the_mail_signed(app, webhook_conn, monkeypatch):
    import hashlib
    import hmac

    from app.crm.webhook import WebhookConnector

    captured = []
    _capture_urlopen(monkeypatch, captured)
    result = WebhookConnector(webhook_conn).log_mail("jan@klant.be", "Vraag", "Body hier")
    assert result == {"logged": True}
    req = captured[0]
    assert req.full_url == "https://hooks.klant.be/otto"
    payload = json.loads(req.data.decode())
    assert payload["event"] == "mail.received" and payload["from"] == "jan@klant.be"
    verwacht = hmac.new(b"webhook-geheim", req.data, hashlib.sha256).hexdigest()
    assert req.get_header("X-otto-signature") == f"sha256={verwacht}"


def test_the_webhook_without_secret_sends_no_signature(app, org, monkeypatch):
    from app.crm.webhook import WebhookConnector

    conn = _connection(org, "webhook", base_url="https://hooks.klant.be/otto")
    captured = []
    _capture_urlopen(monkeypatch, captured)
    assert WebhookConnector(conn).log_mail("jan@klant.be", "x", "y")["logged"] is True
    assert captured[0].get_header("X-otto-signature") is None


def test_a_broken_webhook_never_raises(app, webhook_conn, monkeypatch):
    from app.crm.webhook import WebhookConnector

    captured = []
    _capture_urlopen(monkeypatch, captured, status=500)
    result = WebhookConnector(webhook_conn).log_mail("jan@klant.be", "x", "y")
    assert result["logged"] is False and result["reason"] == "error"
    test = WebhookConnector(webhook_conn).test_connection()
    assert test["ok"] is False and "500" in test["detail"]


# ── De brug vanuit de pijplijn: elke ingeschakelde koppeling krijgt de mail ──
class _Msg:
    def __init__(self, org_id, sender="jan@klant.be"):
        self.organization_id = org_id
        self.sender_email = sender
        self.subject = "Vraag"
        self.body = "Body"
        self.id = 1


def test_the_pipeline_hook_fans_out_to_every_ready_connection(app, org, hubspot_conn,
                                                              webhook_conn, monkeypatch):
    from app.crm import hubspot, webhook
    from app.tasks.crm_log import log_customer_mail

    calls = []
    monkeypatch.setattr(hubspot.HubSpotConnector, "log_mail",
                        lambda self, **kw: calls.append("hubspot") or {"logged": True})
    monkeypatch.setattr(webhook.WebhookConnector, "log_mail",
                        lambda self, **kw: calls.append("webhook") or {"logged": True})
    assert log_customer_mail(_Msg(org.id)) is True
    assert sorted(calls) == ["hubspot", "webhook"]


def test_one_broken_connection_does_not_block_the_next(app, org, hubspot_conn,
                                                       webhook_conn, monkeypatch):
    from app.crm import hubspot, webhook
    from app.tasks.crm_log import log_customer_mail

    def boom(self, **kw):
        raise RuntimeError("HubSpot plat")

    monkeypatch.setattr(hubspot.HubSpotConnector, "log_mail", boom)
    monkeypatch.setattr(webhook.WebhookConnector, "log_mail",
                        lambda self, **kw: {"logged": True})
    assert log_customer_mail(_Msg(org.id)) is True


def test_a_disabled_connection_is_skipped(app, org, webhook_conn, monkeypatch):
    from app.extensions import db
    from app.crm import webhook
    from app.tasks.crm_log import log_customer_mail

    webhook_conn.enabled = False
    db.session.commit()
    called = {"n": 0}
    monkeypatch.setattr(webhook.WebhookConnector, "log_mail",
                        lambda self, **kw: called.__setitem__("n", called["n"] + 1))
    assert log_customer_mail(_Msg(org.id)) is False
    assert called["n"] == 0


# ── Instellingen: opslaan en testen via de webinterface ─────────────────────
def test_saving_a_hubspot_connection_stores_the_token_encrypted(app, auth_client):
    from app.extensions import db
    from app.models import CrmConnection, User

    r = auth_client.post("/app/integrations/crm/hubspot", data={
        "api_key": "pat-eu1-geheim", "enabled": "on", "log_incoming": "on",
    })
    assert r.status_code == 302
    demo = db.session.query(User).filter_by(email="demo@example.com").one()
    conn = db.session.query(CrmConnection).filter_by(
        organization_id=demo.organization_id, provider="hubspot").one()
    assert conn.api_key == "pat-eu1-geheim"
    assert conn.api_key_enc != "pat-eu1-geheim"
    assert conn.is_ready is True

    # Een lege sleutel bij een latere wijziging laat de bestaande sleutel staan.
    r = auth_client.post("/app/integrations/crm/hubspot", data={
        "api_key": "", "enabled": "on", "log_incoming": "",
    })
    assert r.status_code == 302
    db.session.refresh(conn)
    assert conn.api_key == "pat-eu1-geheim" and conn.log_incoming is False


def test_saved_values_show_up_again_in_the_settings_form(app, auth_client):
    """Regressie: de contextsleutel mag niet "values" heten — in Jinja wint de
    dict-methode .values() dan van de sleutel en rendert het formulier leeg."""
    auth_client.post("/app/integrations/crm/webhook", data={
        "base_url": "https://hooks.klant.be/otto", "api_key": "sleutel-xyz-123",
        "enabled": "on", "log_incoming": "on",
    })
    page = auth_client.get("/app/integrations").get_data(as_text=True)
    assert 'value="https://hooks.klant.be/otto"' in page
    assert "Ongewijzigd laten" in page      # sleutel opgeslagen → placeholder, nooit de waarde
    assert "sleutel-xyz-123" not in page    # het geheim zelf komt nooit terug naar de browser


def test_an_unknown_crm_provider_is_a_404(app, auth_client):
    assert auth_client.post("/app/integrations/crm/salesforce", data={}).status_code == 404
    assert auth_client.post("/api/integrations/crm/salesforce/test").status_code == 404


def test_the_test_button_probes_the_real_connection(app, auth_client, monkeypatch):
    from app.crm import hubspot

    # Nog geen koppeling → duidelijke weigering, geen valse OK.
    r = auth_client.post("/api/integrations/crm/hubspot/test")
    assert r.status_code == 400 and r.get_json()["ok"] is False

    auth_client.post("/app/integrations/crm/hubspot", data={
        "api_key": "pat-eu1-geheim", "enabled": "on", "log_incoming": "on",
    })
    monkeypatch.setattr(hubspot.HubSpotConnector, "test_connection",
                        lambda self: {"ok": True, "detail": "HubSpot verbonden."})
    r = auth_client.post("/api/integrations/crm/hubspot/test")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["connection_ok"] is True

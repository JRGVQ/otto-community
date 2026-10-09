"""Gmail realtime via Pub/Sub push: authenticiteit en verwerking.

De kern van deze tests is dat de verificatie *closed* faalt. Een push wordt pas
vertrouwd als Google hem ondertekende én hij van ónze subscription komt — het tweede
is essentieel, want een geldige handtekening bewijst enkel dat íémand met een
Google-project hem stuurde.
"""
from __future__ import annotations

import base64
import json

import pytest

from app.extensions import db
from app.models import MailAccount, User
from app.security.webhook_verify import verify_gmail_push

AUD = "https://otto.example.com/api/webhooks/gmail"
SA = "otto-push@my-project.iam.gserviceaccount.com"


@pytest.fixture()
def realtime(app):
    app.config["GMAIL_PUSH_AUDIENCE"] = AUD
    app.config["GMAIL_PUSH_SA_EMAIL"] = SA
    return app


def _claims(**over):
    base = {"iss": "https://accounts.google.com", "email": SA, "email_verified": True, "aud": AUD}
    base.update(over)
    return base


def _stub_verify(monkeypatch, claims=None, raises=None):
    """Vervang de Google-bibliotheek; de tests raken nooit het netwerk."""
    import sys
    import types

    def verify_oauth2_token(token, request, audience):
        if raises:
            raise raises
        if audience != AUD:
            raise ValueError("verkeerde audience")
        return claims or _claims()

    id_token_mod = types.SimpleNamespace(verify_oauth2_token=verify_oauth2_token)
    requests_mod = types.SimpleNamespace(Request=lambda: object())
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.oauth2", types.SimpleNamespace(id_token=id_token_mod))
    monkeypatch.setitem(sys.modules, "google.auth", types.ModuleType("google.auth"))
    monkeypatch.setitem(sys.modules, "google.auth.transport",
                        types.SimpleNamespace(requests=requests_mod))


def _req(app, token="een-token", body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return app.test_request_context("/api/webhooks/gmail", method="POST",
                                    headers=headers, json=(body or {}))


# ── Verificatie ──────────────────────────────────────────────────────────────
def test_push_is_refused_when_realtime_is_not_configured(app, monkeypatch):
    _stub_verify(monkeypatch)
    app.config["GMAIL_PUSH_AUDIENCE"] = None
    app.config["GMAIL_PUSH_SA_EMAIL"] = None
    with _req(app):
        assert verify_gmail_push(__import__("flask").request) is False


def test_push_is_refused_without_the_service_account_pin(realtime, monkeypatch):
    """Alleen een audience volstaat niet — dan mag iedereen met een Google-project pushen."""
    _stub_verify(monkeypatch)
    realtime.config["GMAIL_PUSH_SA_EMAIL"] = None
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_valid_push_is_accepted(realtime, monkeypatch):
    _stub_verify(monkeypatch)
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is True


def test_push_without_authorization_header_is_refused(realtime, monkeypatch):
    _stub_verify(monkeypatch)
    with _req(realtime, token=None):
        assert verify_gmail_push(__import__("flask").request) is False


def test_push_from_another_service_account_is_refused(realtime, monkeypatch):
    """De handtekening klopt, maar de afzender is niet onze subscription."""
    _stub_verify(monkeypatch, claims=_claims(email="iemand-anders@vreemd.iam.gserviceaccount.com"))
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_push_with_unverified_email_is_refused(realtime, monkeypatch):
    _stub_verify(monkeypatch, claims=_claims(email_verified=False))
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_push_from_a_wrong_issuer_is_refused(realtime, monkeypatch):
    _stub_verify(monkeypatch, claims=_claims(iss="https://evil.example"))
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_bad_signature_is_refused(realtime, monkeypatch):
    _stub_verify(monkeypatch, raises=ValueError("handtekening klopt niet"))
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_push_is_refused_without_google_auth_installed(realtime, monkeypatch):
    """Zonder de bibliotheek kan niets gecontroleerd worden → nooit vertrouwen."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *a, **kw):
        if name.startswith("google"):
            raise ImportError("google-auth ontbreekt")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", blocked)
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is False


def test_service_account_match_is_case_insensitive(realtime, monkeypatch):
    _stub_verify(monkeypatch, claims=_claims(email=SA.upper()))
    with _req(realtime):
        assert verify_gmail_push(__import__("flask").request) is True


# ── Route ────────────────────────────────────────────────────────────────────
def _pubsub_body(address, history_id="12345"):
    data = json.dumps({"emailAddress": address, "historyId": history_id}).encode()
    return {"message": {"data": base64.b64encode(data).decode(), "messageId": "1"}}


@pytest.fixture()
def gmail_account(app):
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="gmail", email="Klant@Gmail.com", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def test_unverified_push_is_acked_and_ignored(client, app):
    """202 in plaats van 4xx: Pub/Sub moet niet blijven herproberen."""
    resp = client.post("/api/webhooks/gmail", json=_pubsub_body("klant@gmail.com"))
    assert resp.status_code == 202


def test_verified_push_enqueues_a_sync(client, realtime, monkeypatch, gmail_account):
    monkeypatch.setattr("app.api.webhooks.verify_gmail_push", lambda r: True)
    synced = []
    monkeypatch.setattr("app.tasks.enqueue.enqueue", lambda fn, *a, **kw: synced.append(a))

    resp = client.post("/api/webhooks/gmail", json=_pubsub_body("klant@gmail.com"),
                       headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204
    assert synced == [(gmail_account.id,)]  # adres matcht ongeacht hoofdletters


def test_push_for_an_unknown_mailbox_is_acked(client, realtime, monkeypatch):
    monkeypatch.setattr("app.api.webhooks.verify_gmail_push", lambda r: True)
    resp = client.post("/api/webhooks/gmail", json=_pubsub_body("niemand@gmail.com"),
                       headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204  # herproberen lost dit niet op


def test_unreadable_payload_is_acked(client, realtime, monkeypatch):
    monkeypatch.setattr("app.api.webhooks.verify_gmail_push", lambda r: True)
    resp = client.post("/api/webhooks/gmail", json={"message": {"data": "geen-base64!!"}},
                       headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204


def test_disconnected_mailbox_is_not_synced(client, realtime, monkeypatch, gmail_account):
    monkeypatch.setattr("app.api.webhooks.verify_gmail_push", lambda r: True)
    gmail_account.status = "disconnected"
    db.session.commit()
    synced = []
    monkeypatch.setattr("app.tasks.enqueue.enqueue", lambda fn, *a, **kw: synced.append(a))

    client.post("/api/webhooks/gmail", json=_pubsub_body("klant@gmail.com"),
                headers={"Authorization": "Bearer x"})
    assert synced == []

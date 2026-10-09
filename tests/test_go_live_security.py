"""Regression tests for the pre-launch security/hardening fixes.

Each test pins one finding from the go-live audit so it can't silently regress.

Note: the `app` fixture (conftest) yields *inside* an active app context, so tests
use `db.session` / `current_app` directly — opening a nested `with app.app_context()`
would create a second session that, on the in-memory StaticPool DB, reads stale data.
"""
from __future__ import annotations

import sys
import types

import pytest

from app import create_app
from app.config import ProdConfig, _DEFAULT_TOKEN_KEY
from app.extensions import db
from app.seed.demo_data import DEMO_PASSWORD


# ── ProdConfig fail-fast guards ──────────────────────────────────────────────
def test_prod_refuses_default_token_key(monkeypatch):
    monkeypatch.setattr(ProdConfig, "SECRET_KEY", "a-real-prod-secret", raising=False)
    monkeypatch.setattr(ProdConfig, "TOKEN_ENCRYPTION_KEY", _DEFAULT_TOKEN_KEY, raising=False)
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        ProdConfig()


def test_prod_config_boots_with_secure_values(monkeypatch):
    monkeypatch.setattr(ProdConfig, "SECRET_KEY", "a-real-prod-secret", raising=False)
    monkeypatch.setattr(ProdConfig, "TOKEN_ENCRYPTION_KEY", "a-unique-non-default-key", raising=False)
    ProdConfig()  # must not raise


# ── Seed never creates a known-credential super-admin in production ──────────
def test_seed_creates_no_platform_owner_in_prod():
    prod_app = create_app("testing")
    prod_app.config.update(DEBUG=False, TESTING=False)  # simulate a production boot
    with prod_app.app_context():
        from app.models import User
        from app.seed import seed_all

        db.drop_all()
        db.create_all()
        result = seed_all()
        assert db.session.query(User).filter_by(is_platform_owner=True).count() == 0
        assert result["password"] != DEMO_PASSWORD  # randomised in prod
        db.session.remove()
        db.drop_all()


# ── Privilege escalation: change_role must not grant the owner role ──────────
def test_change_role_rejects_owner(auth_client, app):
    from app.models import User

    target = db.session.query(User).filter_by(email="pieter@acme.be").first()
    tid, before = target.id, target.role_id
    resp = auth_client.post(f"/api/admin/users/{tid}/role", json={"role": "Eigenaar"})
    assert resp.status_code == 403
    assert db.session.get(User, tid).role_id == before


# ── Read-only users cannot mutate via the write APIs ────────────────────────
def test_readonly_user_cannot_edit_drafts(auth_client, app):
    from app.models import Draft, Role, User

    ro = db.session.query(Role).filter_by(name="Alleen-lezen").first()
    demo = db.session.query(User).filter_by(email="demo@example.com").first()
    demo.role_id = ro.id
    db.session.commit()
    mid = db.session.query(Draft).first().message_id
    resp = auth_client.put(f"/api/drafts/{mid}", json={"text": "should be blocked"})
    assert resp.status_code == 403


# ── Open redirect: off-host ?next must be ignored ───────────────────────────
def test_login_ignores_offsite_next(app):
    for bad in ("https://evil.example/", "//evil.example/x"):
        c = app.test_client()
        resp = c.post(
            f"/auth/login?next={bad}",
            data={"email": "demo@example.com", "password": DEMO_PASSWORD},
        )
        assert resp.status_code in (301, 302, 303)
        assert "evil.example" not in resp.headers.get("Location", "")


# ── AI budget metering: OpenAI/Azure adapters must report token usage ────────
def _fake_openai_module():
    mod = types.ModuleType("openai")

    class _Usage:
        prompt_tokens = 12
        completion_tokens = 34

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _Resp:
        usage = _Usage()
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_Completions())

    mod.OpenAI = _Client
    mod.AzureOpenAI = _Client
    return mod


def test_openai_adapter_records_usage(app, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module())
    app.config["OPENAI_API_KEY"] = "x"
    from app.ai.openai_adapter import OpenAIAdapter

    adapter = OpenAIAdapter()
    adapter.complete("system", "user")
    assert adapter.last_usage == (12, 34)


def test_azure_adapter_records_usage(app, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module())
    app.config["AZURE_OPENAI_ENDPOINT"] = "https://x.openai.azure.com"
    app.config["AZURE_OPENAI_API_KEY"] = "x"
    from app.ai.azure_adapter import AzureAdapter

    adapter = AzureAdapter()
    adapter.complete("system", "user")
    assert adapter.last_usage == (12, 34)


def test_budget_cap_trips_after_recorded_usage(app):
    from app.ai.metering import record_usage, within_budget
    from app.models import Organization

    app.config["LLM_MONTHLY_BUDGET_EUR"] = 50
    org = db.session.query(Organization).first()
    assert within_budget(org) is True
    record_usage(org.id, "gpt-4o", 50_000_000, 50_000_000)  # far past the budget
    assert within_budget(org) is False


# ── Webhook authenticity fails closed ───────────────────────────────────────
def test_graph_webhook_verification_fails_closed(app):
    from app.security.webhook_verify import verify_graph_notification

    with app.test_request_context(json={"value": [{"clientState": "s3cret"}]}):
        from flask import request

        assert verify_graph_notification(request, None) is False      # no secret configured
        assert verify_graph_notification(request, "s3cret") is True   # exact match
        assert verify_graph_notification(request, "wrong") is False   # mismatch


def test_gmail_webhook_verification_fails_closed(app):
    from app.security.webhook_verify import verify_gmail_push

    with app.test_request_context():
        from flask import request

        assert verify_gmail_push(request) is False


# ── Registration must not reveal whether an account exists ──────────────────
def test_register_does_not_leak_account_existence(client):
    resp = client.post(
        "/auth/register",
        data={
            "company": "Acme Two", "name": "Iemand", "email": "demo@example.com",
            "password": "geheim123", "confirm": "geheim123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"bestaat al" not in resp.data.lower()

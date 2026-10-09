"""Bring-your-own LLM key per organisation.

Verifies provider selection, the no-key fallback, the save route and that an own key
keeps working on an instance without a shared model.
"""
from __future__ import annotations

import app.ai as ai_pkg
from app.auth.service import create_company
from app.extensions import db
from app.security.crypto import decrypt, encrypt


class _FakeAdapter:
    name = "anthropic"
    model_name = "fake"
    last_usage = (0, 0)

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def complete(self, *a, **k):
        return "ok"


def _org(email="o@byo.be"):
    user = create_company("BYO BV", "Owner", email, "geheim123")
    return user.organization


# ── get_service_for: BYO key wins, no key → the instance model (stub in tests) ──
def test_byo_key_is_used_when_set(app, monkeypatch):
    import app.ai.anthropic_adapter as aa
    monkeypatch.setattr(aa, "AnthropicAdapter", _FakeAdapter)

    org = _org()
    org.ai.byo_provider = "anthropic"
    org.ai.byo_api_key_enc = encrypt("sk-customer-key")
    org.ai.byo_model = "claude-sonnet-4-6"
    db.session.commit()

    service = ai_pkg.get_service_for(org)
    assert isinstance(service.adapter, _FakeAdapter)
    assert service.adapter.kwargs["api_key"] == "sk-customer-key"   # decrypted + injected
    assert service.adapter.kwargs["model"] == "claude-sonnet-4-6"
    assert service.is_stub is False


def test_without_key_uses_the_instance_model(app):
    org = _org()
    service = ai_pkg.get_service_for(org)
    # LLM_PROVIDER=stub in testing → the instance path returns the stub (no crash, no BYO).
    assert service.is_stub is True


def test_byo_compatible_uses_openai_with_base_url(app, monkeypatch):
    import app.ai.openai_adapter as oa
    monkeypatch.setattr(oa, "OpenAIAdapter", _FakeAdapter)

    org = _org(email="o3@compat.be")
    org.ai.byo_provider = "compatible"
    org.ai.byo_api_key_enc = encrypt("sk-or-key")
    org.ai.byo_base_url = "https://openrouter.ai/api/v1"
    org.ai.byo_model = "mistral-large"
    db.session.commit()

    service = ai_pkg.get_service_for(org)
    assert service.adapter.kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert service.adapter.kwargs["api_key"] == "sk-or-key"


# ── Save route encrypts + stores; clearing works ────────────────────────────
def test_byo_save_route_stores_encrypted_key(auth_client, app):
    from app.models import Organization

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    resp = auth_client.post("/admin/ai/byo", data={
        "provider": "anthropic", "api_key": "sk-secret-123", "model": "claude-sonnet-4-6",
    })
    assert resp.status_code in (302, 303)
    ai = db.session.query(Organization).filter_by(id=org.id).first().ai
    assert ai.byo_provider == "anthropic"
    assert ai.byo_api_key_enc and ai.byo_api_key_enc != "sk-secret-123"  # stored encrypted
    assert decrypt(ai.byo_api_key_enc) == "sk-secret-123"

    # Clearing the provider wipes the key.
    auth_client.post("/admin/ai/byo", data={"provider": ""})
    ai = db.session.query(Organization).filter_by(id=org.id).first().ai
    assert ai.byo_provider is None and ai.byo_api_key_enc is None


def test_byo_save_blank_key_keeps_stored_one(auth_client, app):
    from app.models import Organization

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    auth_client.post("/admin/ai/byo", data={"provider": "openai", "api_key": "sk-first"})
    # Re-save without a key (e.g. only changing the model) keeps the stored key.
    auth_client.post("/admin/ai/byo", data={"provider": "openai", "api_key": "", "model": "gpt-4o"})
    ai = db.session.query(Organization).filter_by(id=org.id).first().ai
    assert decrypt(ai.byo_api_key_enc) == "sk-first"
    assert ai.byo_model == "gpt-4o"


# ── An own key powers drafts, also without an instance-wide model ────────────
def test_own_key_generates_draft(app, monkeypatch):
    import app.ai.anthropic_adapter as aa
    monkeypatch.setattr(aa, "AnthropicAdapter", _FakeAdapter)
    from app.models import Message, Organization
    from app.tasks.draft import generate_draft

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    org.ai.byo_provider = "anthropic"
    org.ai.byo_api_key_enc = encrypt("sk-x")
    db.session.commit()
    msg = db.session.query(Message).filter_by(organization_id=org.id).first()
    if msg.draft:
        db.session.delete(msg.draft)
        db.session.commit()

    did = generate_draft(msg.id, force=True)
    assert did is not None
    db.session.refresh(msg)
    assert msg.draft and msg.draft.current_text == "ok"  # generated via their key


def test_no_model_banner_points_admins_to_the_ai_screen(auth_client, app):
    """Without an instance model and without an own key, admins see where to add one."""
    resp = auth_client.get("/app/inbox")
    assert resp.status_code == 200
    assert b"Er is nog geen AI-model gekoppeld" in resp.data


def test_no_model_banner_hidden_once_a_key_is_set(auth_client, app):
    from app.models import Organization

    org = db.session.query(Organization).filter_by(is_demo=True).first()
    org.ai.byo_provider = "anthropic"
    org.ai.byo_api_key_enc = encrypt("sk-x")
    db.session.commit()
    resp = auth_client.get("/app/inbox")
    assert b"Er is nog geen AI-model gekoppeld" not in resp.data

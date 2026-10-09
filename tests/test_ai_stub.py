"""Stub LLM: deterministic categorisation + per-tone drafts."""
from app.ai.service import LLMService
from app.ai.stub_adapter import StubAdapter
from app.constants import TONES

CATS = [
    {"key": "te_beantwoorden", "name": "Te beantwoorden", "ai": ""},
    {"key": "fyi", "name": "FYI", "ai": ""},
    {"key": "nieuwsbrief", "name": "Nieuwsbrief", "ai": ""},
    {"key": "meldingen", "name": "Meldingen", "ai": ""},
    {"key": "spam", "name": "Spam", "ai": ""},
]


def svc():
    return LLMService(StubAdapter())


def test_categorize_spam():
    r = svc().categorize("U heeft €1.000.000 gewonnen!!!", "win@x.info", "GEFELICITEERD winnaar", CATS)
    assert r["category_key"] == "spam"


def test_categorize_notification():
    r = svc().categorize("Beveiligingsmelding: nieuwe aanmelding", "no-reply@ms.com", "nieuwe aanmelding", CATS)
    assert r["category_key"] == "meldingen"


def test_categorize_newsletter():
    r = svc().categorize("This week in web design", "mail@smashing.com", "unsubscribe newsletter", CATS)
    assert r["category_key"] == "nieuwsbrief"


def test_categorize_default_reply():
    r = svc().categorize("Offerte website", "klant@bedrijf.be", "Kun je een prijs geven?", CATS)
    assert r["category_key"] == "te_beantwoorden"
    assert 0 <= r["confidence"] <= 100


def test_draft_per_tone_differs():
    s = svc()
    texts = {t: s.draft("Marlies Decroix", "Offerte", "Graag een prijs", t, None) for t in TONES}
    assert len(set(texts.values())) == len(TONES)  # all four tones produce distinct drafts
    assert texts["formeel"].startswith("Geachte")
    assert "Marlies" in texts["neutraal"]


# ── Provider selection (real Claude vs stub) ─────────────────────────────────
def test_provider_falls_back_to_stub_without_key(app):
    """LLM_PROVIDER=anthropic but no key must never crash a request — fall back to stub."""
    from app.ai import get_adapter

    app.config["LLM_PROVIDER"] = "anthropic"
    app.config["ANTHROPIC_API_KEY"] = None
    assert get_adapter().name == "stub"


def test_anthropic_adapter_selected_with_key(app):
    """With the SDK installed + a key, the real Claude adapter is built on claude-opus-4-8."""
    import pytest

    pytest.importorskip("anthropic")  # skips in the minimal CI env (requirements.txt only)
    from app.ai import get_adapter

    app.config["LLM_PROVIDER"] = "anthropic"
    app.config["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"
    adapter = get_adapter()
    assert adapter.name == "anthropic" and adapter._model == "claude-opus-4-8"

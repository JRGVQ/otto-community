"""LLM abstraction. `get_llm()` picks an adapter from config; default is the stub."""
from __future__ import annotations

from flask import current_app

from app.ai.base import LLMAdapter


def get_adapter() -> LLMAdapter:
    provider = (current_app.config.get("LLM_PROVIDER") or "stub").lower()
    try:
        if provider == "anthropic":
            from app.ai.anthropic_adapter import AnthropicAdapter

            return AnthropicAdapter()
        if provider == "openai":
            from app.ai.openai_adapter import OpenAIAdapter

            return OpenAIAdapter()
        if provider == "azure":
            from app.ai.azure_adapter import AzureAdapter

            return AzureAdapter()
    except Exception as exc:  # missing SDK/key → fall back, never crash a request
        current_app.logger.warning("LLM provider %s unavailable (%s); using stub.", provider, exc)
    from app.ai.stub_adapter import StubAdapter

    return StubAdapter()


def get_service():
    from app.ai.service import LLMService

    return LLMService(get_adapter())


def org_byo_provider(org) -> str | None:
    """The org's configured bring-your-own LLM provider, if a key is present (else None)."""
    ai = getattr(org, "ai", None) if org is not None else None
    if ai and ai.byo_provider and ai.byo_api_key_enc:
        return ai.byo_provider
    return None


def _byo_adapter(org):
    """Build an adapter from the org's OWN LLM credentials, or None."""
    ai = getattr(org, "ai", None) if org is not None else None
    if not (ai and ai.byo_provider and ai.byo_api_key_enc):
        return None
    from app.security.crypto import decrypt

    try:
        key = decrypt(ai.byo_api_key_enc)
        if not key:
            return None
        provider = (ai.byo_provider or "").lower()
        model, base_url = (ai.byo_model or None), (ai.byo_base_url or None)
        if provider == "anthropic":
            from app.ai.anthropic_adapter import AnthropicAdapter

            return AnthropicAdapter(api_key=key, model=model)
        if provider in ("openai", "compatible"):
            from app.ai.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(api_key=key, model=model, base_url=base_url)
        if provider == "azure":
            from app.ai.azure_adapter import AzureAdapter

            return AzureAdapter(api_key=key, endpoint=base_url, deployment=model)
    except Exception as exc:  # noqa: BLE001 - bad key / missing SDK → fall back, never crash
        current_app.logger.warning("BYO LLM adapter unavailable for org %s (%s).", getattr(org, "id", "?"), exc)
    return None


def get_service_for(org):
    """Pick the AI service for this org:
      1. The org's OWN LLM key (set under Beheer → AI & model) → use it, uncapped.
      2. Otherwise → the instance-wide LLM (LLM_PROVIDER), while the org is within the
         optional monthly budget (LLM_MONTHLY_BUDGET_EUR); over it → the stub.
    """
    from app.ai.metering import within_budget
    from app.ai.service import LLMService
    from app.ai.stub_adapter import StubAdapter

    byo = _byo_adapter(org)
    if byo is not None:
        return LLMService(byo)

    provider = (current_app.config.get("LLM_PROVIDER") or "stub").lower()
    if provider == "stub":
        return LLMService(get_adapter())
    if org is not None and not within_budget(org):
        current_app.logger.info("Org %s over AI budget this month; using stub.", getattr(org, "id", "?"))
        return LLMService(StubAdapter())
    return LLMService(get_adapter())

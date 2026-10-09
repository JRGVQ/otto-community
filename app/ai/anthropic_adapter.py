"""Anthropic Claude adapter (lazy import)."""
from __future__ import annotations

from flask import current_app

from app.ai.base import LLMAdapter


class AnthropicAdapter(LLMAdapter):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from anthropic import Anthropic  # raises ImportError if absent

        key = api_key or current_app.config.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")
        self._client = Anthropic(api_key=key)
        self._model = model or current_app.config.get("LLM_MODEL") or "claude-opus-4-8"
        self.model_name = self._model
        self.last_usage = (0, 0)  # (input_tokens, output_tokens) of the last call

    def complete(self, system: str, user: str, *, max_tokens: int = 800, json: bool = False) -> str:
        msg = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(msg, "usage", None)
        if usage is not None:
            self.last_usage = (getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")

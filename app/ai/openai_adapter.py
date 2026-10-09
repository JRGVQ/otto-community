"""OpenAI adapter (lazy import)."""
from __future__ import annotations

from flask import current_app

from app.ai.base import LLMAdapter


class OpenAIAdapter(LLMAdapter):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> None:
        from openai import OpenAI

        key = api_key or current_app.config.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY ontbreekt")
        # base_url lets any OpenAI-compatible provider plug in (Mistral, Groq, Together,
        # OpenRouter, DeepSeek, Gemini's OpenAI endpoint, local servers, …).
        self._client = OpenAI(api_key=key, base_url=base_url) if base_url else OpenAI(api_key=key)
        self._model = model or current_app.config.get("LLM_MODEL") or "gpt-4o"
        self.model_name = self._model
        self.last_usage = (0, 0)  # (input_tokens, output_tokens) of the last call

    def complete(self, system: str, user: str, *, max_tokens: int = 800, json: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json else {}
        resp = self._client.chat.completions.create(
            model=self._model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.last_usage = (getattr(usage, "prompt_tokens", 0) or 0,
                               getattr(usage, "completion_tokens", 0) or 0)
        return resp.choices[0].message.content or ""

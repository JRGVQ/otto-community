"""Azure OpenAI adapter (EU data residency) — lazy import."""
from __future__ import annotations

from flask import current_app

from app.ai.base import LLMAdapter


class AzureAdapter(LLMAdapter):
    name = "azure"

    def __init__(self, api_key: str | None = None, endpoint: str | None = None, deployment: str | None = None) -> None:
        from openai import AzureOpenAI

        endpoint = endpoint or current_app.config.get("AZURE_OPENAI_ENDPOINT")
        key = api_key or current_app.config.get("AZURE_OPENAI_API_KEY")
        if not (endpoint and key):
            raise RuntimeError("AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY ontbreken")
        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=key, api_version="2024-06-01")
        self._deployment = deployment or current_app.config.get("AZURE_OPENAI_DEPLOYMENT") or "gpt-4o"
        self.model_name = self._deployment
        self.last_usage = (0, 0)  # (input_tokens, output_tokens) of the last call

    def complete(self, system: str, user: str, *, max_tokens: int = 800, json: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json else {}
        resp = self._client.chat.completions.create(
            model=self._deployment, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.last_usage = (getattr(usage, "prompt_tokens", 0) or 0,
                               getattr(usage, "completion_tokens", 0) or 0)
        return resp.choices[0].message.content or ""

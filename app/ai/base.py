"""LLM adapter contract."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMAdapter(ABC):
    name = "base"

    @abstractmethod
    def complete(self, system: str, user: str, *, max_tokens: int = 800, json: bool = False) -> str:
        """Return the model completion for a system + user prompt."""

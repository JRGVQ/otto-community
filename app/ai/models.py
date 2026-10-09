"""The model picker's catalogue for the bring-your-own-key providers.

Model ids used to be a free-text field, which meant a single typo ("claude-sonnet4")
silently produced a provider error on the first real e-mail rather than at the moment of
saving. This is the curated list the settings screen and the onboarding flow both offer.

Two deliberate choices:

* **A custom entry always stays available.** Provider line-ups move faster than a release
  of this app, and Azure "models" are deployment names the customer invents themselves —
  a closed list would be a dead end. `CUSTOM` is the escape hatch.
* **A stored value that is not in the list is never dropped.** A saved model we don't
  offer simply opens the form on "Anders…" with the value filled in, so viewing the
  settings screen can never silently rewrite a working configuration.

Prices/tiers are intentionally described in plain language ("sneller en goedkoper")
rather than exact rates — rates change, and the customer pays the provider directly.
"""
from __future__ import annotations

from flask_babel import lazy_gettext as _l

CUSTOM = "__custom__"

# Per provider: (model id, human label). Ordered best-default-first — the first entry of
# each provider is what a customer gets by picking the obvious top option.
CATALOGUE: dict[str, list[tuple[str, str]]] = {
    "anthropic": [
        ("claude-opus-5", "Claude Opus 5 — beste kwaliteit"),
        ("claude-sonnet-5", "Claude Sonnet 5 — sneller en goedkoper"),
        ("claude-haiku-4-5", "Claude Haiku 4.5 — snelst en goedkoopst"),
        ("claude-opus-4-8", "Claude Opus 4.8 — vorige generatie"),
    ],
    "openai": [
        ("gpt-4o", "GPT-4o — beste kwaliteit"),
        ("gpt-4o-mini", "GPT-4o mini — sneller en goedkoper"),
    ],
    # Azure exposes *deployments* the customer names themselves, so there is no useful
    # list to offer — only the custom field makes sense here.
    "azure": [],
    # Anything OpenAI-compatible (Mistral, Groq, OpenRouter, a local model…): the id
    # depends entirely on the endpoint, so likewise custom-only.
    "compatible": [],
}

# Shown under the field, per provider, so a customer knows where to find the id.
HINTS: dict[str, str] = {
    "azure": _l("Vul de naam van je Azure-deployment in (die kies je zelf in Azure OpenAI)."),
    "compatible": _l("Vul het model-id in zoals jouw endpoint het noemt, bv. "
                     "mistral-large-latest of meta-llama/llama-3.1-70b-instruct."),
}


def choices_for(provider: str | None) -> list[tuple[str, str]]:
    """(id, label) options for one provider, always ending in the custom escape hatch."""
    return [*CATALOGUE.get((provider or "").lower(), []), (CUSTOM, _l("Anders — zelf invullen…"))]


def catalogue_json() -> dict[str, list[dict[str, str]]]:
    """The whole catalogue in a shape the settings screen's script can consume, so the
    options can be swapped client-side when the provider dropdown changes."""
    return {
        provider: [{"id": model_id, "label": str(label)} for model_id, label in models]
        for provider, models in CATALOGUE.items()
    }


def resolve_submitted(model: str | None, custom: str | None) -> str | None:
    """The model id to store from a submitted form ('custom' picked → the typed value)."""
    model = (model or "").strip()
    if model == CUSTOM:
        return (custom or "").strip() or None
    return model or None


def is_known(provider: str | None, model: str | None) -> bool:
    """True when `model` is one of the ids we offer for `provider`."""
    return any(model_id == model for model_id, _ in CATALOGUE.get((provider or "").lower(), []))


def template_context(ai) -> dict:
    """Everything `_byo_model_field.html` / `_byo_model_script.html` need.

    Shared by the AI settings screen and the onboarding flow so the two can never drift
    apart on which models they offer.
    """
    provider = getattr(ai, "byo_provider", None) if ai else None
    model = getattr(ai, "byo_model", None) if ai else None
    return {
        "byo_model_choices": choices_for(provider),
        "byo_model_catalogue": catalogue_json(),
        "byo_model_hints": {key: str(text) for key, text in HINTS.items()},
        "byo_model_custom": CUSTOM,
        # A saved model we don't list (or a custom-only provider) opens the form on
        # "Anders…" with the value already filled in.
        "byo_model_is_custom": bool(model) and not is_known(provider, model),
    }

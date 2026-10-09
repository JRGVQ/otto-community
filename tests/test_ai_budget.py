"""AI usage metering + the optional per-organisation monthly budget cap
(LLM_MONTHLY_BUDGET_EUR — unset means no cap)."""

from app.ai.metering import (
    budget_eur,
    cost_eur,
    month_cost_eur,
    month_usage,
    record_usage,
    within_budget,
)
from app.ai.service import LLMService


class FakeAdapter:
    """Pretends to be a real provider: every complete() reports token usage."""

    name = "fake"
    model_name = "claude-sonnet-4-6"

    def __init__(self):
        self.last_usage = (0, 0)

    def complete(self, system, user, *, max_tokens=800, json=False):
        self.last_usage = (1000, 500)
        return '{"category_key": "te_beantwoorden", "confidence": 80}' if json else "Beste,\n\nDank.\n\nGroet"


CATS = [{"key": "te_beantwoorden", "name": "Te beantwoorden", "ai": ""}]


# ── Pricing maths ────────────────────────────────────────────────────────────
def test_cost_eur_known_model(app):
    # sonnet = $3 in / $15 out per 1M, ×0.95 USD→EUR
    assert round(cost_eur("claude-sonnet-4-6", 1_000_000, 1_000_000), 2) == 17.10


def test_cost_eur_unknown_model_uses_default(app):
    assert cost_eur("something-new", 0, 0) == 0.0


# ── Metering accumulation ────────────────────────────────────────────────────
def test_record_usage_accumulates_per_month(app, demo_user):
    org = demo_user.organization
    record_usage(org.id, "claude-sonnet-4-6", 1000, 500)
    record_usage(org.id, "claude-sonnet-4-6", 2000, 1000)
    assert month_usage(org) == (3000, 1500)


def test_record_usage_ignores_empty(app, demo_user):
    org = demo_user.organization
    record_usage(org.id, "claude-sonnet-4-6", 0, 0)
    assert month_usage(org) == (0, 0)


# ── Budget gate ──────────────────────────────────────────────────────────────
def test_no_budget_configured_means_no_cap(app, demo_user):
    app.config["LLM_MONTHLY_BUDGET_EUR"] = None
    org = demo_user.organization
    assert budget_eur(org) is None
    record_usage(org.id, "claude-sonnet-4-6", 0, 10_000_000)  # a lot of usage
    assert within_budget(org) is True


def test_budget_is_read_from_config(app, demo_user):
    app.config["LLM_MONTHLY_BUDGET_EUR"] = 25
    assert budget_eur(demo_user.organization) == 25.0


def test_within_budget_flips_when_exceeded(app, demo_user):
    app.config["LLM_MONTHLY_BUDGET_EUR"] = 8
    org = demo_user.organization
    assert within_budget(org) is True
    record_usage(org.id, "claude-sonnet-4-6", 0, 1_000_000)  # ≈ €14.25 > €8
    assert within_budget(org) is False
    assert month_cost_eur(org) > budget_eur(org)


def test_get_service_for_falls_back_to_stub_over_budget(app, demo_user):
    from app.ai import get_service_for

    app.config["LLM_MONTHLY_BUDGET_EUR"] = 8
    org = demo_user.organization
    app.config["LLM_PROVIDER"] = "anthropic"  # an instance-wide provider is configured…
    record_usage(org.id, "claude-sonnet-4-6", 0, 1_000_000)  # …but the org is over budget
    assert get_service_for(org).is_stub  # → falls back to the stub


def test_get_service_for_stub_provider_always_stub(app, demo_user):
    from app.ai import get_service_for

    app.config["LLM_PROVIDER"] = "stub"
    assert get_service_for(demo_user.organization).is_stub


# ── Service captures adapter usage ───────────────────────────────────────────
def test_service_accumulates_and_pops_usage(app):
    svc = LLMService(FakeAdapter())
    svc.categorize("Offerte", "k@x.be", "Prijs?", CATS)
    assert svc.pop_usage() == (1000, 500)
    assert svc.pop_usage() == (0, 0)  # reset after pop
    assert svc.model_name == "claude-sonnet-4-6"


def test_service_accumulates_across_draft_tones(app):
    from app.constants import TONES

    svc = LLMService(FakeAdapter())
    for t in TONES:
        svc.draft("Marlies", "Offerte", "Prijs?", t, None)
    assert svc.pop_usage() == (1000 * len(TONES), 500 * len(TONES))

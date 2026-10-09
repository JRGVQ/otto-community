"""AI usage metering + an optional per-organisation monthly budget cap.

Every real LLM call on the instance-wide key is metered per org/month, so the
administrator can see what each organisation costs. Set LLM_MONTHLY_BUDGET_EUR to cap
it: once an org's AI cost for the month reaches that amount, its pipeline falls back
to the free stub until the next month. Unset (the default) = no cap.
"""
from __future__ import annotations

from flask import current_app
from sqlalchemy import func

from app.models.base import utcnow

# USD per 1M tokens (input, output) — keep in sync with the model you run.
MODEL_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}
DEFAULT_PRICE = (3.0, 15.0)

def _ym() -> str:
    return utcnow().strftime("%Y-%m")


def cost_eur(model: str, in_tok: int, out_tok: int) -> float:
    p_in, p_out = MODEL_PRICES.get(model, DEFAULT_PRICE)
    usd = (in_tok / 1_000_000) * p_in + (out_tok / 1_000_000) * p_out
    return usd * float(current_app.config.get("LLM_USD_EUR", 0.95))


def record_usage(org_id: int, model: str, in_tok: int, out_tok: int) -> None:
    """Add this call's tokens to the org's running total for the month.

    The increment is a single atomic UPDATE (`SET input_tokens = input_tokens + :n`)
    rather than read-modify-write in Python. That difference matters: two workers
    drafting at the same time both read the same starting value, and the second
    commit would overwrite the first — silently losing usage. Undercounting is worse
    than an overshoot, because the budget cap then under-enforces every month, not
    just briefly.
    """
    if not org_id or not (in_tok or out_tok):
        return
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models import AiUsage

    ym, in_tok, out_tok = _ym(), int(in_tok), int(out_tok)
    model = (model or "").strip()[:64]

    def _bump() -> int:
        """Atomic increment; returns how many rows it touched (0 = no row yet)."""
        return (
            db.session.query(AiUsage)
            .filter_by(organization_id=org_id, ym=ym, model=model)
            .update(
                {
                    AiUsage.input_tokens: func.coalesce(AiUsage.input_tokens, 0) + in_tok,
                    AiUsage.output_tokens: func.coalesce(AiUsage.output_tokens, 0) + out_tok,
                },
                synchronize_session=False,
            )
        )

    if _bump():
        db.session.commit()
        return

    # First call this month for this model → create the row. A parallel worker may win
    # that race, and the unique key on (organization_id, ym, model) makes the loser notice
    # instead of writing a duplicate: fall back to incrementing the row that now exists.
    db.session.add(AiUsage(organization_id=org_id, ym=ym, model=model,
                           input_tokens=in_tok, output_tokens=out_tok))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _bump()
        db.session.commit()


def month_rows(org) -> list:
    """This month's usage rows for `org` — one per model that was actually used."""
    from app.extensions import db
    from app.models import AiUsage

    if org is None:
        return []
    return db.session.query(AiUsage).filter_by(organization_id=org.id, ym=_ym()).all()


def month_usage(org):
    """Total (input, output) tokens this month, across every model."""
    rows = month_rows(org)
    return (sum(r.input_tokens or 0 for r in rows), sum(r.output_tokens or 0 for r in rows))


def month_cost_eur(org) -> float:
    """This month's AI cost in EUR, priced per model.

    Each row is charged at the model that produced it. Pricing the whole month at the
    currently configured model would be wrong the moment the model changes — and since
    prices differ by an order of magnitude between the small and large models, the
    budget cap would then fire far too early or far too late.
    """
    fallback = current_app.config.get("LLM_MODEL") or "claude-sonnet-4-6"
    return sum(
        cost_eur(row.model or fallback, row.input_tokens or 0, row.output_tokens or 0)
        for row in month_rows(org)
    )


def budget_eur(org) -> float | None:
    """The monthly AI budget per organisation in EUR (LLM_MONTHLY_BUDGET_EUR), or None
    when no cap is configured."""
    raw = current_app.config.get("LLM_MONTHLY_BUDGET_EUR")
    try:
        value = float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    return value if value and value > 0 else None


def within_budget(org) -> bool:
    """True when the org may still use the instance LLM this month (always, without a cap).

    This is a check-then-act gate: it is read before the call and the tokens are booked
    after it, so calls already in flight when the cap is reached still complete. The
    overshoot is bounded by (concurrent calls × cost of one call) — with drafts capped
    at 600 output tokens that is a fraction of a cent, so it is accepted rather than
    serialised behind a lock.

    What is *not* accepted is losing usage: record_usage increments in SQL precisely so
    the counter this gate reads stays accurate. An undercounted month would make the cap
    under-enforce every month, which is a far bigger leak than a brief overshoot.
    """
    budget = budget_eur(org)
    return budget is None or month_cost_eur(org) < budget

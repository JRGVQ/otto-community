"""Per-organisation monthly AI token usage — meters spend to enforce a budget cap."""
from __future__ import annotations

from app.extensions import db


class AiUsage(db.Model):
    """Tokens an organisation spent in one month, per model.

    Per model, not just per month: prices differ by an order of magnitude (Haiku vs
    Opus), so pricing a month's tokens against whatever model happens to be configured
    today would misprice every month in which the model changed — and the budget cap
    rides on that number.
    """

    __tablename__ = "ai_usage"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "ym", "model", name="uq_ai_usage_org_month_model"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    ym = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    # The model these tokens were actually billed at. "" for rows written before usage
    # was split per model; those are priced at the configured model, as they were before.
    model = db.Column(db.String(64), nullable=False, default="", server_default="")
    input_tokens = db.Column(db.BigInteger, default=0)
    output_tokens = db.Column(db.BigInteger, default=0)

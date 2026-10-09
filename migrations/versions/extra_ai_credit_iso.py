"""extra AI-tegoed per organisatie (EUR/maand bovenop het plafond van het plan)

Revision ID: extra_ai_credit_iso
Revises: noise_filter_iso
Create Date: 2026-08-22

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "extra_ai_credit_iso"
down_revision = "noise_filter_iso"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "extra_ai_credit_eur" not in _columns("licenses"):
        with op.batch_alter_table("licenses") as batch:
            batch.add_column(sa.Column("extra_ai_credit_eur", sa.Numeric(10, 2), nullable=True))


def downgrade():
    if "extra_ai_credit_eur" in _columns("licenses"):
        with op.batch_alter_table("licenses") as batch:
            batch.drop_column("extra_ai_credit_eur")

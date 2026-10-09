"""ai_usage.model — verbruik per model, zodat elke maand correct geprijsd wordt

Revision ID: ai_usage_model_iso
Revises: signatures_iso
Create Date: 2026-08-19

Prijzen verschillen een orde van grootte tussen de kleine en grote modellen. Zolang het
verbruik alleen per maand werd bijgehouden, werd een hele maand afgerekend tegen het
model dat toevallig ingesteld stond — en de budgetcap rijdt op dat getal mee.

Bestaande rijen krijgen "" als model: die worden geprijsd tegen het geconfigureerde
model, precies zoals ze voordien al werden berekend. Zo verandert er niets met terugwerkende
kracht; enkel nieuw verbruik wordt per model apart bijgehouden.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "ai_usage_model_iso"
down_revision = "signatures_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def _constraint_names(table):
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_unique_constraints(table)}


def upgrade():
    if _has_column("ai_usage", "model"):
        return
    # batch_alter_table = SQLite-veilig; het herbouwt de tabel met de nieuwe sleutel.
    with op.batch_alter_table("ai_usage") as batch:
        batch.add_column(sa.Column("model", sa.String(length=64), nullable=False,
                                   server_default=""))
        if "uq_ai_usage_org_month" in _constraint_names("ai_usage"):
            batch.drop_constraint("uq_ai_usage_org_month", type_="unique")
        batch.create_unique_constraint(
            "uq_ai_usage_org_month_model", ["organization_id", "ym", "model"])


def downgrade():
    if not _has_column("ai_usage", "model"):
        return
    with op.batch_alter_table("ai_usage") as batch:
        if "uq_ai_usage_org_month_model" in _constraint_names("ai_usage"):
            batch.drop_constraint("uq_ai_usage_org_month_model", type_="unique")
        batch.drop_column("model")
        batch.create_unique_constraint("uq_ai_usage_org_month", ["organization_id", "ym"])

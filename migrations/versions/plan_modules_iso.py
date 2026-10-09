"""licenties: à la carte modules (Pro/Teams/Handtekeningen) + handtekening-facturatiemodus

Revision ID: plan_modules_iso
Revises: sig_perconfig_iso
Create Date: 2026-09-10

Voegt licenties.mod_pro / mod_teams / mod_signatures (nullable bool = "gebruik de
plan-standaard") + sig_billing_mode toe, zodat modules los per organisatie aan/uit kunnen
en Odoo ze via de entitlement-webhook zet. Additief en niet-brekend: NULL = huidig gedrag.

Idempotent met _has_table/_has_column-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "plan_modules_iso"
down_revision = "sig_perconfig_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has_table("licenses"):
        return
    with op.batch_alter_table("licenses") as batch:
        if not _has_column("licenses", "mod_pro"):
            batch.add_column(sa.Column("mod_pro", sa.Boolean(), nullable=True))
        if not _has_column("licenses", "mod_teams"):
            batch.add_column(sa.Column("mod_teams", sa.Boolean(), nullable=True))
        if not _has_column("licenses", "mod_signatures"):
            batch.add_column(sa.Column("mod_signatures", sa.Boolean(), nullable=True))
        if not _has_column("licenses", "sig_billing_mode"):
            batch.add_column(sa.Column("sig_billing_mode", sa.String(length=16), nullable=True))


def downgrade():
    if not _has_table("licenses"):
        return
    with op.batch_alter_table("licenses") as batch:
        for col in ("sig_billing_mode", "mod_signatures", "mod_teams", "mod_pro"):
            if _has_column("licenses", col):
                batch.drop_column(col)

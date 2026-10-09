"""brand kits: social-profielen ({{social}}-placeholder)

Revision ID: sig_social_iso
Revises: banner_stats_iso
Create Date: 2026-09-07

Voegt brand_kits.socials (JSON: netwerk→URL) toe. Voedt de e-mailveilige {{social}}-
placeholder (profiel-links in de accentkleur). Geen live feeds (die vergen API-tokens).

Idempotent met _has_table/_has_column-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_social_iso"
down_revision = "banner_stats_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if _has_table("brand_kits") and not _has_column("brand_kits", "socials"):
        with op.batch_alter_table("brand_kits") as batch:
            batch.add_column(sa.Column("socials", sa.JSON(), nullable=True))


def downgrade():
    if _has_column("brand_kits", "socials"):
        with op.batch_alter_table("brand_kits") as batch:
            batch.drop_column("socials")

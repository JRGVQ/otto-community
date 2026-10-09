"""brand kits: centrale huisstijl (logo + accentkleur) per organisatie/domein

Revision ID: brand_kits_iso
Revises: sig_banner_scope_iso
Create Date: 2026-09-07

Eén tabel brand_kits: per organisatie een standaard-huisstijl (domain NULL) en optioneel
een eigen huisstijl per verzenddomein. Voedt de {{logo}}- en {{merk_kleur}}-placeholders.

Idempotent met _has_table-guard. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "brand_kits_iso"
down_revision = "sig_banner_scope_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("brand_kits"):
        op.create_table(
            "brand_kits",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("domain", sa.String(length=128), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("logo_url", sa.String(length=512), nullable=True),
            sa.Column("accent_color", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "domain", name="uq_brandkit_org_domain"),
        )
        with op.batch_alter_table("brand_kits") as batch:
            batch.create_index("ix_brand_kits_organization_id", ["organization_id"])


def downgrade():
    if _has_table("brand_kits"):
        op.drop_table("brand_kits")

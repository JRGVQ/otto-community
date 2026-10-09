"""banner-analytics: opt-in meting (weergaven + clicks) per banner/dag

Revision ID: banner_stats_iso
Revises: brand_kits_iso
Create Date: 2026-09-07

Voegt signature_banners.track (opt-in) toe en banner_stats (dagelijkse aggregaat-teller
per banner: weergaven + clicks; geen persoonsgegevens). Voedt het campagne-dashboard.

Idempotent met _has_table/_has_column-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "banner_stats_iso"
down_revision = "brand_kits_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if _has_table("signature_banners") and not _has_column("signature_banners", "track"):
        with op.batch_alter_table("signature_banners") as batch:
            batch.add_column(sa.Column("track", sa.Boolean(), nullable=False, server_default=sa.false()))

    if not _has_table("banner_stats"):
        op.create_table(
            "banner_stats",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("banner_id", sa.Integer(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["banner_id"], ["signature_banners.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("banner_id", "day", name="uq_bannerstat_banner_day"),
        )
        with op.batch_alter_table("banner_stats") as batch:
            batch.create_index("ix_banner_stats_organization_id", ["organization_id"])
            batch.create_index("ix_banner_stats_banner_id", ["banner_id"])


def downgrade():
    if _has_table("banner_stats"):
        op.drop_table("banner_stats")
    if _has_column("signature_banners", "track"):
        with op.batch_alter_table("signature_banners") as batch:
            batch.drop_column("track")

"""cloud-modus voor handtekeningen (Exchange transport-regel) — per organisatie

Revision ID: cloud_sig_iso
Revises: sig_assets_photos_iso
Create Date: 2026-09-04

Fase 5 van de handtekening-uitbreiding: cloud_signature_settings (afleveringsmodus
client/cloud/combo per organisatie + de aansturende handtekening, marker en attestatie).

NB: hangt aan de fase-2-head (sig_assets_photos_iso); deze branch is los van fase 3/4
opgezet. Landen die eerder op main, dan moet deze migratie achter hun head geketend
worden (merge origin/main + herketen; single head bewaren).

Idempotent met een _has_table-guard. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "cloud_sig_iso"
down_revision = "sig_ar_banners_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("cloud_signature_settings"):
        op.create_table(
            "cloud_signature_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("mode", sa.String(length=8), nullable=False, server_default="client"),
            sa.Column("signature_id", sa.Integer(), nullable=True),
            sa.Column("marker", sa.String(length=32), nullable=False, server_default="otto-sig-v1"),
            sa.Column("rule_attested_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", name="uq_cloud_sig_org"),
        )
        with op.batch_alter_table("cloud_signature_settings") as batch:
            batch.create_index("ix_cloud_signature_settings_organization_id", ["organization_id"])


def downgrade():
    if _has_table("cloud_signature_settings"):
        op.drop_table("cloud_signature_settings")

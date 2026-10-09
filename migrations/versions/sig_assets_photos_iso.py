"""handtekening-assets + profielfoto's + gekoppeld M365-attribuut op variabelen

Revision ID: sig_assets_photos_iso
Revises: sig_templates_iso
Create Date: 2026-09-03

Fase 2 van de handtekening-uitbreiding:
  - signature_assets: logo's/banners die als CID-bijlage in de aflevering meegaan;
  - user_photos: gecachte M365-profielfoto per gebruiker (voor {{foto}});
  - signature_variables.graph_attr: koppelt een variabele aan een Graph-/Entra-attribuut.

Idempotent: _has_table-guards en een kolom-bestaat-guard rond de add_column, plus
batch_alter_table (SQLite). Hangt aan de fase-1-head; single head blijft behouden.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_assets_photos_iso"
down_revision = "sig_templates_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has_table("signature_assets"):
        op.create_table(
            "signature_assets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("signature_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=True),
            sa.Column("content_type", sa.String(length=64), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("signature_assets") as batch:
            batch.create_index("ix_signature_assets_organization_id", ["organization_id"])

    if not _has_table("user_photos"):
        op.create_table(
            "user_photos",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("content_type", sa.String(length=64), nullable=False),
            sa.Column("etag", sa.String(length=128), nullable=True),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("user_id"),
        )
        with op.batch_alter_table("user_photos") as batch:
            batch.create_index("ix_user_photos_organization_id", ["organization_id"])

    if not _has_column("signature_variables", "graph_attr"):
        with op.batch_alter_table("signature_variables") as batch:
            batch.add_column(sa.Column("graph_attr", sa.String(length=128), nullable=True))


def downgrade():
    if _has_column("signature_variables", "graph_attr"):
        with op.batch_alter_table("signature_variables") as batch:
            batch.drop_column("graph_attr")
    if _has_table("user_photos"):
        op.drop_table("user_photos")
    if _has_table("signature_assets"):
        op.drop_table("signature_assets")

"""handtekening-sjablonen (galerij): door de organisatie geïmporteerde ontwerpen

Revision ID: sig_templates_iso
Revises: rename_starter_personal
Create Date: 2026-09-03

Eén tabel signature_templates: herbruikbare handtekening-sjablonen per organisatie.
De vier ingebouwde sjablonen blijven in Python (app/signature_templates.py); deze tabel
bevat alléén de door een organisatie geïmporteerde ('custom') ontwerpen — schema-only,
dus geen seed. Idempotent met een _has_table-guard, zoals de andere *_iso-migraties.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_templates_iso"
# Achter rename_starter_personal geketend (PR #115 landde parallel op main, óók vanaf
# crm_directory_iso) zodat er één migratie-head blijft.
down_revision = "rename_starter_personal"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("signature_templates"):
        op.create_table(
            "signature_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("html", sa.Text(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "slug", name="uq_sig_tpl_org_slug"),
        )
        with op.batch_alter_table("signature_templates") as batch:
            batch.create_index("ix_signature_templates_organization_id", ["organization_id"])


def downgrade():
    if _has_table("signature_templates"):
        op.drop_table("signature_templates")

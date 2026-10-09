"""signatures — handtekeningen per organisatie, optioneel per verzenddomein

Revision ID: signatures_iso
Revises: org_events_iso
Create Date: 2026-08-19

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "signatures_iso"
down_revision = "org_events_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("signatures"):
        return
    op.create_table(
        "signatures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "domain", name="uq_signature_org_domain"),
    )
    with op.batch_alter_table("signatures") as batch:
        batch.create_index("ix_signatures_organization_id", ["organization_id"])
        batch.create_index("ix_signatures_domain", ["domain"])


def downgrade():
    if _has_table("signatures"):
        op.drop_table("signatures")

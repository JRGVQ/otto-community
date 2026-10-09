"""domain_claims — DNS-TXT domeinverificatie per organisatie

Revision ID: domain_claims_iso
Revises: user_intro_seen_iso
Create Date: 2026-08-19

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "domain_claims_iso"
down_revision = "user_intro_seen_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("domain_claims"):
        return
    op.create_table(
        "domain_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=128), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "domain", name="uq_domain_claim_org_domain"),
    )
    with op.batch_alter_table("domain_claims") as batch:
        batch.create_index("ix_domain_claims_organization_id", ["organization_id"])
        batch.create_index("ix_domain_claims_domain", ["domain"])
        batch.create_index("ix_domain_claims_verified_at", ["verified_at"])


def downgrade():
    if _has_table("domain_claims"):
        op.drop_table("domain_claims")

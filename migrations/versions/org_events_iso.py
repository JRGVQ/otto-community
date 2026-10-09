"""org_events — zakelijke tijdlijn per organisatie (geen mailboxinhoud)

Revision ID: org_events_iso
Revises: domain_claims_iso
Create Date: 2026-08-19

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "org_events_iso"
down_revision = "domain_claims_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("org_events"):
        return
    op.create_table(
        "org_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=48), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("org_events") as batch:
        batch.create_index("ix_org_events_organization_id", ["organization_id"])
        batch.create_index("ix_org_events_event", ["event"])
        batch.create_index("ix_org_events_occurred_at", ["occurred_at"])


def downgrade():
    if _has_table("org_events"):
        op.drop_table("org_events")

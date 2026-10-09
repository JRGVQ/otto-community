"""License override_until — platform-owner 30-day grace window

Lets the platform owner un-block an expired/suspended company for 30 days. After it
lapses the company is blocked again and directed to support; a scheduled task
soft-deletes companies blocked 60+ days (see app/tasks/schedule.py).

Revision ID: license_override_until_iso
Revises: f2a3b4c5d6e1
Create Date: 2026-06-29

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "license_override_until_iso"
# Chains after the odoo-portal migration (main's head) — NOT directly after
# per_org_roles_iso — so the branch merges into main with a single alembic head.
down_revision = "f2a3b4c5d6e1"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    # Idempotent for safety: the database has known migration drift from parallel
    # collision-fix branches, so guard the add against an already-present column.
    if not _has_column("licenses", "override_until"):
        with op.batch_alter_table("licenses", schema=None) as batch:
            batch.add_column(sa.Column("override_until", sa.Date(), nullable=True))


def downgrade():
    if _has_column("licenses", "override_until"):
        with op.batch_alter_table("licenses", schema=None) as batch:
            batch.drop_column("override_until")

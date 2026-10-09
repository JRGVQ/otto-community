"""users.intro_seen — post-onboarding intro ("Rondleiding") shown once

Revision ID: user_intro_seen_iso
Revises: request_logs_iso
Create Date: 2026-08-13

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "user_intro_seen_iso"
down_revision = "request_logs_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("users", "intro_seen"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(sa.Column("intro_seen", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    if _has_column("users", "intro_seen"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("intro_seen")

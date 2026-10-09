"""Comprehensive access log (request_logs) + platform_settings key/value store

request_logs: one row per served HTTP request across every environment, so the owner
backoffice ("Logboek") can trace what happened before an error — including 5xx's that
never reach the Python error handler (proxy/worker timeouts). platform_settings backs the
owner-configurable log retention window.

Revision ID: request_logs_iso
Revises: followups_foldering_iso
Create Date: 2026-08-13

NB: revision id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32),
dus een langere id crasht op de UPDATE alembic_version-stap.

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "request_logs_iso"
down_revision = "followups_foldering_iso"
branch_labels = None
depends_on = None


def _has_table(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    if not _has_table("request_logs"):
        op.create_table(
            "request_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("site", sa.String(length=16), nullable=True),
            sa.Column("method", sa.String(length=8), nullable=True),
            sa.Column("path", sa.String(length=512), nullable=True),
            sa.Column("endpoint", sa.String(length=128), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("org_name", sa.String(length=128), nullable=True),
            sa.Column("user_email", sa.String(length=255), nullable=True),
            sa.Column("ip", sa.String(length=64), nullable=True),
            sa.Column("request_id", sa.String(length=36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_request_logs_occurred_at", "request_logs", ["occurred_at"])
        op.create_index("ix_request_logs_status_code", "request_logs", ["status_code"])

    if not _has_table("platform_settings"):
        op.create_table(
            "platform_settings",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )


def downgrade():
    if _has_table("platform_settings"):
        op.drop_table("platform_settings")
    if _has_table("request_logs"):
        op.drop_index("ix_request_logs_status_code", table_name="request_logs")
        op.drop_index("ix_request_logs_occurred_at", table_name="request_logs")
        op.drop_table("request_logs")

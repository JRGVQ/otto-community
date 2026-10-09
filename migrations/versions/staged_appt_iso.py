"""Klaargezette afspraken — wachtrij voor bevestigde momenten (fase 6.4b)

Revision ID: staged_appt_iso
Revises: teams_bundle_iso
Create Date: 2026-08-24

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "staged_appt_iso"
down_revision = "teams_bundle_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("staged_appointments"):
        op.create_table(
            "staged_appointments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("mail_account_id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("provider_thread_id", sa.String(length=255), nullable=True),
            sa.Column("source_message_id", sa.Integer(), nullable=True),
            sa.Column("subject", sa.String(length=512), nullable=True),
            sa.Column("counterparty", sa.String(length=255), nullable=True),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("duration_min", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("event_id", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["mail_account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("staged_appointments") as batch:
            batch.create_index("ix_staged_appointments_mail_account_id", ["mail_account_id"])
            batch.create_index("ix_staged_appointments_provider_thread_id", ["provider_thread_id"])


def downgrade():
    if _has_table("staged_appointments"):
        op.drop_table("staged_appointments")

"""knowledge_entries — bedrijfskennis die de klant zelf invoert (werkt zonder M365)

Revision ID: knowledge_entries_iso
Revises: mail_rules_iso
Create Date: 2026-08-21

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "knowledge_entries_iso"
down_revision = "mail_rules_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("knowledge_entries"):
        return
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.create_index("ix_knowledge_entries_organization_id", ["organization_id"])
        batch.create_index("ix_knowledge_entries_account_id", ["account_id"])


def downgrade():
    if _has_table("knowledge_entries"):
        op.drop_table("knowledge_entries")

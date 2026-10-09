"""snippets — herbruikbare tekstblokken per organisatie of gedeelde mailbox

Revision ID: snippets_iso
Revises: ai_usage_model_iso
Create Date: 2026-08-19

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "snippets_iso"
down_revision = "ai_usage_model_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("snippets"):
        return
    op.create_table(
        "snippets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("snippets") as batch:
        batch.create_index("ix_snippets_organization_id", ["organization_id"])
        batch.create_index("ix_snippets_account_id", ["account_id"])


def downgrade():
    if _has_table("snippets"):
        op.drop_table("snippets")

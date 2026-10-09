"""mail_rules — inbox-regels die Otto beheert en Outlook uitvoert

Revision ID: mail_rules_iso
Revises: outlook_cat_num_iso
Create Date: 2026-08-21

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "mail_rules_iso"
down_revision = "outlook_cat_num_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("mail_rules"):
        return
    op.create_table(
        "mail_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("conditions", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("provider_rule_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("mail_rules") as batch:
        batch.create_index("ix_mail_rules_organization_id", ["organization_id"])
        batch.create_index("ix_mail_rules_account_id", ["account_id"])


def downgrade():
    if _has_table("mail_rules"):
        op.drop_table("mail_rules")

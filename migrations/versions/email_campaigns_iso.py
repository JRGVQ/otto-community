"""E-mail campaigns: email_campaigns + campaign_recipients tables + users.marketing_opt_out

Lets the platform owner compose a branded mail and send it to a segment of customers,
with per-recipient tracking and a marketing opt-out (unsubscribe).

Revision ID: email_campaigns_iso
Revises: unsubscribe_iso
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "email_campaigns_iso"
down_revision = "unsubscribe_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_table(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    if not _has_column("users", "marketing_opt_out"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(sa.Column("marketing_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()))

    if not _has_table("email_campaigns"):
        op.create_table(
            "email_campaigns",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("body_html", sa.Text(), nullable=False),
            sa.Column("segment", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False),
            sa.Column("sent_count", sa.Integer(), nullable=False),
            sa.Column("failed_count", sa.Integer(), nullable=False),
            sa.Column("skipped_count", sa.Integer(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("campaign_recipients"):
        op.create_table(
            "campaign_recipients",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["campaign_id"], ["email_campaigns.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"])


def downgrade():
    if _has_table("campaign_recipients"):
        op.drop_table("campaign_recipients")
    if _has_table("email_campaigns"):
        op.drop_table("email_campaigns")
    if _has_column("users", "marketing_opt_out"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("marketing_opt_out")

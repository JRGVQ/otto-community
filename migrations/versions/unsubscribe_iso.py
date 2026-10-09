"""List-Unsubscribe support: messages.unsubscribe_* columns + unsubscribe_logs table

Stores the parsed List-Unsubscribe (RFC 2369 / 8058) info per message so the inbox can
offer an "Uitschrijven" action, plus a durable log of every unsubscribe attempt.

Revision ID: unsubscribe_iso
Revises: b7d1e9f4a2c6
Create Date: 2026-07-06

Chained after b7d1e9f4a2c6 (the Odoo-portal merge migration) rather than directly after
msg_provider_thread_id_iso: both landed on main independently and shared that parent, which
left Alembic with two heads ("Multiple head revisions") and blocked deploys. The two
migrations are independent, so ordering unsubscribe after the merge is safe.

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "unsubscribe_iso"
down_revision = "b7d1e9f4a2c6"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_table(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    with op.batch_alter_table("messages", schema=None) as batch:
        if not _has_column("messages", "unsubscribe_url"):
            batch.add_column(sa.Column("unsubscribe_url", sa.String(length=1024), nullable=True))
        if not _has_column("messages", "unsubscribe_mailto"):
            batch.add_column(sa.Column("unsubscribe_mailto", sa.String(length=512), nullable=True))
        if not _has_column("messages", "unsubscribe_oneclick"):
            batch.add_column(sa.Column("unsubscribe_oneclick", sa.Boolean(), nullable=True))
        if not _has_column("messages", "unsubscribed_at"):
            batch.add_column(sa.Column("unsubscribed_at", sa.DateTime(), nullable=True))

    if not _has_table("unsubscribe_logs"):
        op.create_table(
            "unsubscribe_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("message_id", sa.Integer(), nullable=True),
            sa.Column("sender", sa.String(length=128), nullable=True),
            sa.Column("sender_email", sa.String(length=255), nullable=True),
            sa.Column("method", sa.String(length=16), nullable=True),
            sa.Column("target", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_unsubscribe_logs_organization_id", "unsubscribe_logs", ["organization_id"])
        op.create_index("ix_unsubscribe_logs_sender_email", "unsubscribe_logs", ["sender_email"])
        op.create_index("ix_unsubscribe_logs_created_at", "unsubscribe_logs", ["created_at"])


def downgrade():
    if _has_table("unsubscribe_logs"):
        op.drop_index("ix_unsubscribe_logs_created_at", table_name="unsubscribe_logs")
        op.drop_index("ix_unsubscribe_logs_sender_email", table_name="unsubscribe_logs")
        op.drop_index("ix_unsubscribe_logs_organization_id", table_name="unsubscribe_logs")
        op.drop_table("unsubscribe_logs")
    with op.batch_alter_table("messages", schema=None) as batch:
        for col in ("unsubscribed_at", "unsubscribe_oneclick", "unsubscribe_mailto", "unsubscribe_url"):
            if _has_column("messages", col):
                batch.drop_column(col)

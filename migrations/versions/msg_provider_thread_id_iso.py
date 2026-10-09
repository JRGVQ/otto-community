"""messages.provider_thread_id — store the raw conversation id

Lets the "already answered" guard match a whole conversation (Graph conversationId /
Gmail threadId). The thread_id FK is left NULL for synced mail, so it can't serve this.

Revision ID: msg_provider_thread_id_iso
Revises: graph_admin_consent_iso
Create Date: 2026-06-30

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "msg_provider_thread_id_iso"
down_revision = "graph_admin_consent_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("messages", "provider_thread_id"):
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.add_column(sa.Column("provider_thread_id", sa.String(length=255), nullable=True))
            batch.create_index("ix_messages_provider_thread_id", ["provider_thread_id"])


def downgrade():
    if _has_column("messages", "provider_thread_id"):
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.drop_index("ix_messages_provider_thread_id")
            batch.drop_column("provider_thread_id")

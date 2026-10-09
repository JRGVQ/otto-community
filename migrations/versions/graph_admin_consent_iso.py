"""organizations.graph_admin_consented_at — one-click M365 admin consent

Records when a tenant admin granted Otto tenant-wide consent via the one-click
admin-consent flow, so the Integraties page can show an approved state and users
can then connect their mailbox without a per-user consent prompt.

Revision ID: graph_admin_consent_iso
Revises: widen_sync_cursor_to_text
Create Date: 2026-06-30

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "graph_admin_consent_iso"
# Chains after the sync_cursor widen (current main head) so the branch stays a single
# alembic head after merge.
down_revision = "widen_sync_cursor_to_text"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    # Idempotent: the DB has known migration drift from parallel collision-fix branches.
    if not _has_column("organizations", "graph_admin_consented_at"):
        with op.batch_alter_table("organizations", schema=None) as batch:
            batch.add_column(sa.Column("graph_admin_consented_at", sa.DateTime(), nullable=True))


def downgrade():
    if _has_column("organizations", "graph_admin_consented_at"):
        with op.batch_alter_table("organizations", schema=None) as batch:
            batch.drop_column("graph_admin_consented_at")

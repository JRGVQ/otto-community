"""mail_accounts.last_error — surface sync/connection failures

Adds a nullable text column to record the last sync/connection error per mailbox, so a
failing fetch is visible in Integraties (and the connection test) instead of looking
like 'no new mail'.

Revision ID: mailaccount_last_error_iso
Revises: license_override_until_iso
Create Date: 2026-06-29

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# Slug id (not rotation-hex) to avoid the hand-typed-id collisions seen earlier.
revision = "mailaccount_last_error_iso"
down_revision = "license_override_until_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("mail_accounts", "last_error"):
        with op.batch_alter_table("mail_accounts", schema=None) as batch:
            batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))


def downgrade():
    if _has_column("mail_accounts", "last_error"):
        with op.batch_alter_table("mail_accounts", schema=None) as batch:
            batch.drop_column("last_error")

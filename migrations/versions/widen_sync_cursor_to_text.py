"""Widen mail_accounts.sync_cursor from VARCHAR(255) to TEXT

A Microsoft Graph deltaLink (stored as the sync cursor for Outlook mailboxes) is a
full URL carrying an opaque folder-id plus a $deltatoken — routinely 350-500 chars.
The original String(255) silently accepts this on SQLite (dev) but raises
"value too long for type character varying(255)" on Postgres (prod), which
sync_account() swallows and rolls back — so every Outlook sync discarded its newly
fetched mail and the inbox never updated. Text removes the bound (Gmail's short
historyId is unaffected). Stuck accounts self-heal on their next poll.

Revision ID: widen_sync_cursor_to_text
Revises: mailaccount_last_error_iso
Create Date: 2026-06-30

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "widen_sync_cursor_to_text"
# Chains after mail_accounts.last_error (main's head) so the branch merges with a
# single alembic head — NOT directly after license_override_until_iso, which would
# fork into two heads alongside mailaccount_last_error_iso.
down_revision = "mailaccount_last_error_iso"
branch_labels = None
depends_on = None


def _sync_cursor_is_bounded() -> bool:
    """True while the column is still a length-bounded VARCHAR (i.e. not yet TEXT).

    Idempotent guard: the DB has known migration drift from parallel collision-fix
    branches, and re-running a type-widen must be a no-op once it has landed.
    """
    insp = sa.inspect(op.get_bind())
    for col in insp.get_columns("mail_accounts"):
        if col["name"] == "sync_cursor":
            return getattr(col["type"], "length", None) is not None
    return False


def upgrade():
    if _sync_cursor_is_bounded():
        with op.batch_alter_table("mail_accounts", schema=None) as batch:
            batch.alter_column(
                "sync_cursor",
                existing_type=sa.String(length=255),
                type_=sa.Text(),
                existing_nullable=True,
            )


def downgrade():
    # Re-narrowing would truncate live delta cursors and reintroduce the bug, so the
    # downgrade is intentionally a no-op (TEXT is a strict superset of VARCHAR(255)).
    pass

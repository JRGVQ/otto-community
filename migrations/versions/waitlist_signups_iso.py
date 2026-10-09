"""wachtlijst: aanvragen van kantoren (waitlist_signups)

Revision ID: waitlist_signups_iso
Revises: plan_modules_iso
Create Date: 2026-09-14

Nieuwe tabel voor de publieke wachtlijst. E-mail is UNIEK: twee keer inzenden mag de
publieke teller niet opblazen. Additief — raakt geen bestaande tabel.

Idempotent met een _has_table-guard. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "waitlist_signups_iso"
down_revision = "plan_modules_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table("waitlist_signups"):
        return
    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=160), nullable=True),
        sa.Column("segment", sa.String(length=32), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="wachtend"),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_waitlist_signups_email"),
    )
    op.create_index("ix_waitlist_signups_email", "waitlist_signups", ["email"])
    op.create_index("ix_waitlist_signups_status", "waitlist_signups", ["status"])


def downgrade():
    if not _has_table("waitlist_signups"):
        return
    op.drop_index("ix_waitlist_signups_status", table_name="waitlist_signups")
    op.drop_index("ix_waitlist_signups_email", table_name="waitlist_signups")
    op.drop_table("waitlist_signups")

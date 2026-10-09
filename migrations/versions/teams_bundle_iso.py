"""Teams-meldingen bundelen — wachtrij + instellingen per gedeelde mailbox

Revision ID: teams_bundle_iso
Revises: sig_analytics_merge_iso
Create Date: 2026-08-23

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "teams_bundle_iso"
down_revision = "sig_analytics_merge_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    cols = _columns("mailbox_settings")
    with op.batch_alter_table("mailbox_settings") as batch:
        if "teams_bundle" not in cols:
            batch.add_column(sa.Column("teams_bundle", sa.Boolean(), nullable=True,
                                       server_default=sa.false()))
        if "teams_bundle_hours" not in cols:
            batch.add_column(sa.Column("teams_bundle_hours", sa.String(length=32), nullable=True))
        if "teams_last_flush_at" not in cols:
            batch.add_column(sa.Column("teams_last_flush_at", sa.DateTime(), nullable=True))

    if not _has_table("teams_pending"):
        op.create_table(
            "teams_pending",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("mail_account_id", sa.Integer(), nullable=False),
            sa.Column("subject", sa.String(length=512), nullable=True),
            sa.Column("sender", sa.String(length=255), nullable=True),
            sa.Column("category_name", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["mail_account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("teams_pending") as batch:
            batch.create_index("ix_teams_pending_mail_account_id", ["mail_account_id"])


def downgrade():
    if _has_table("teams_pending"):
        op.drop_table("teams_pending")
    cols = _columns("mailbox_settings")
    with op.batch_alter_table("mailbox_settings") as batch:
        for naam in ("teams_last_flush_at", "teams_bundle_hours", "teams_bundle"):
            if naam in cols:
                batch.drop_column(naam)

"""toezeggingen — per postvak aan/uit + herkomst van een automatisch afgeleide taak

Revision ID: commitments_iso
Revises: propose_slots_iso
Create Date: 2026-08-22

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "commitments_iso"
down_revision = "propose_slots_iso"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for tabel in ("style_profiles", "mailbox_settings"):
        if "commitments_enabled" not in _columns(tabel):
            with op.batch_alter_table(tabel) as batch:
                batch.add_column(sa.Column("commitments_enabled", sa.Boolean(), nullable=True,
                                           server_default=sa.false()))
    if "source_sent_message_id" not in _columns("tasks"):
        with op.batch_alter_table("tasks") as batch:
            batch.add_column(sa.Column("source_sent_message_id", sa.String(length=255), nullable=True))
            batch.create_index("ix_tasks_source_sent_message_id", ["source_sent_message_id"])


def downgrade():
    if "source_sent_message_id" in _columns("tasks"):
        with op.batch_alter_table("tasks") as batch:
            batch.drop_index("ix_tasks_source_sent_message_id")
            batch.drop_column("source_sent_message_id")
    for tabel in ("style_profiles", "mailbox_settings"):
        if "commitments_enabled" in _columns(tabel):
            with op.batch_alter_table(tabel) as batch:
                batch.drop_column("commitments_enabled")

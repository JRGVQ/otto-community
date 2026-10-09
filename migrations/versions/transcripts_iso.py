"""Vergadertranscripties → actiepunten: opt-in per postvak (fase 6.6)

Revision ID: transcripts_iso
Revises: odoo_conn_iso
Create Date: 2026-08-24

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "transcripts_iso"
down_revision = "odoo_conn_iso"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for tabel in ("style_profiles", "mailbox_settings"):
        if "transcripts_enabled" not in _columns(tabel):
            with op.batch_alter_table(tabel) as batch:
                batch.add_column(sa.Column("transcripts_enabled", sa.Boolean(), nullable=True,
                                           server_default=sa.false()))


def downgrade():
    for tabel in ("mailbox_settings", "style_profiles"):
        if "transcripts_enabled" in _columns(tabel):
            with op.batch_alter_table(tabel) as batch:
                batch.drop_column("transcripts_enabled")

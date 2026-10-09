"""ruisfilter + digest-modus — ruis per categorie, filter en vaste momenten per postvak

Revision ID: noise_filter_iso
Revises: knowledge_entries_iso
Create Date: 2026-08-21

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "noise_filter_iso"
down_revision = "knowledge_entries_iso"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "is_noise" not in _columns("categories"):
        with op.batch_alter_table("categories") as batch:
            batch.add_column(sa.Column("is_noise", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    if "noise_filter_enabled" not in _columns("style_profiles"):
        with op.batch_alter_table("style_profiles") as batch:
            batch.add_column(sa.Column("noise_filter_enabled", sa.Boolean(), nullable=True,
                                       server_default=sa.false()))
    cols = _columns("style_profiles")
    if "digest_mode" not in cols:
        with op.batch_alter_table("style_profiles") as batch:
            batch.add_column(sa.Column("digest_mode", sa.Boolean(), nullable=True,
                                       server_default=sa.false()))
    if "digest_hours" not in cols:
        with op.batch_alter_table("style_profiles") as batch:
            batch.add_column(sa.Column("digest_hours", sa.String(length=32), nullable=True))
    if "noise_filter_enabled" not in _columns("mailbox_settings"):
        with op.batch_alter_table("mailbox_settings") as batch:
            batch.add_column(sa.Column("noise_filter_enabled", sa.Boolean(), nullable=True,
                                       server_default=sa.false()))


def downgrade():
    for column in ("digest_hours", "digest_mode"):
        if column in _columns("style_profiles"):
            with op.batch_alter_table("style_profiles") as batch:
                batch.drop_column(column)
    for table in ("mailbox_settings", "style_profiles"):
        if "noise_filter_enabled" in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("noise_filter_enabled")
    if "is_noise" in _columns("categories"):
        with op.batch_alter_table("categories") as batch:
            batch.drop_column("is_noise")

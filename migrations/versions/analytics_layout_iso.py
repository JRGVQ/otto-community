"""users.analytics_layout — volgorde + verborgen widgets op het Analyse-bord, per gebruiker

JSON-tekst ({"order": [...], "hidden": [...]}); NULL betekent de standaardindeling.
Bewust geen eigen tabel: één klein voorkeursblob per gebruiker, altijd in zijn geheel
gelezen en geschreven.

Revision ID: analytics_layout_iso
Revises: commitments_iso
Create Date: 2026-08-23

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "analytics_layout_iso"
down_revision = "commitments_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("users", "analytics_layout"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(sa.Column("analytics_layout", sa.Text(), nullable=True))


def downgrade():
    if _has_column("users", "analytics_layout"):
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("analytics_layout")

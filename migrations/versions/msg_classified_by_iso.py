"""messages.classified_by — wie de categorie zette: filter (zonder AI), ai, of user

Maakt het ruis-rapport exact: alleen mail die het ruisfilter zónder AI-aanroep
wegzette telt als bespaarde classificatie. NULL voor bestaande rijen — daarvan
weten we het niet, en gokken zou het besparingscijfer opblazen.

Revision ID: msg_classified_by_iso
Revises: teams_bundle_iso
Create Date: 2026-08-23

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "msg_classified_by_iso"
down_revision = "teams_bundle_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("messages", "classified_by"):
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.add_column(sa.Column("classified_by", sa.String(length=16), nullable=True))


def downgrade():
    if _has_column("messages", "classified_by"):
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.drop_column("classified_by")

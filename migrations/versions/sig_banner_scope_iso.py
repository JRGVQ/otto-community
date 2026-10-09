"""banner-targeting: doelbereik per domein / afdeling / M365-groep

Revision ID: sig_banner_scope_iso
Revises: sig_central_iso
Create Date: 2026-09-07

Voegt scope_kind + scope_value toe aan signature_banners, zodat een banner gericht kan
worden op iedereen (org), een verzenddomein, een afdeling of een Microsoft 365-groep —
nodig voor tenants met meerdere domeinen/afdelingen/groepen.

Idempotent met kolom-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_banner_scope_iso"
down_revision = "sig_central_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if _has_table("signature_banners"):
        with op.batch_alter_table("signature_banners") as batch:
            if not _has_column("signature_banners", "scope_kind"):
                batch.add_column(sa.Column("scope_kind", sa.String(length=16), nullable=False,
                                           server_default="org"))
            if not _has_column("signature_banners", "scope_value"):
                batch.add_column(sa.Column("scope_value", sa.String(length=255), nullable=True))


def downgrade():
    if _has_table("signature_banners"):
        with op.batch_alter_table("signature_banners") as batch:
            if _has_column("signature_banners", "scope_value"):
                batch.drop_column("scope_value")
            if _has_column("signature_banners", "scope_kind"):
                batch.drop_column("scope_kind")

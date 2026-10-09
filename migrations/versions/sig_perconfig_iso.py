"""handtekeningen: per-handtekening-config (aan/uit, tijdvenster, ontvanger-scope, uitzonderingen)

Revision ID: sig_perconfig_iso
Revises: sig_social_iso
Create Date: 2026-09-07

Voegt signatures.enabled / starts_at / ends_at / recipient_scope / exclusions toe. Zo is elke
handtekening apart te targeten en te plannen; buiten haar venster neemt de standaard weer over.

Idempotent met _has_table/_has_column-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_perconfig_iso"
down_revision = "sig_social_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _uniques(table):
    try:
        return {u["name"] for u in sa.inspect(op.get_bind()).get_unique_constraints(table)}
    except Exception:  # noqa: BLE001
        return set()


def upgrade():
    if not _has_table("signatures"):
        return
    # De oude unieke sleutel (org, domein) verhinderde meerdere handtekeningen per domein.
    # Die laten we los: een permanente standaard + geplande campagnes (met tijdvenster) mogen
    # samen bestaan; de app bewaakt dat hun actieve periodes niet overlappen.
    drop_uq = "uq_signature_org_domain" in _uniques("signatures")
    with op.batch_alter_table("signatures") as batch:
        if not _has_column("signatures", "enabled"):
            batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        if not _has_column("signatures", "starts_at"):
            batch.add_column(sa.Column("starts_at", sa.DateTime(), nullable=True))
        if not _has_column("signatures", "ends_at"):
            batch.add_column(sa.Column("ends_at", sa.DateTime(), nullable=True))
        if not _has_column("signatures", "recipient_scope"):
            batch.add_column(sa.Column("recipient_scope", sa.String(length=16), nullable=False, server_default="all"))
        if not _has_column("signatures", "exclusions"):
            batch.add_column(sa.Column("exclusions", sa.JSON(), nullable=True))
        if drop_uq:
            batch.drop_constraint("uq_signature_org_domain", type_="unique")


def downgrade():
    if not _has_table("signatures"):
        return
    with op.batch_alter_table("signatures") as batch:
        for col in ("exclusions", "recipient_scope", "ends_at", "starts_at", "enabled"):
            if _has_column("signatures", col):
                batch.drop_column(col)

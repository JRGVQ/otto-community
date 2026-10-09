"""agenda — vrije momenten voorstellen (per postvak aan/uit + duur)

Revision ID: propose_slots_iso
Revises: extra_ai_credit_iso
Create Date: 2026-08-22

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "propose_slots_iso"
down_revision = "extra_ai_credit_iso"
branch_labels = None
depends_on = None

_KOLOMMEN = (
    ("propose_slots_enabled", lambda: sa.Column("propose_slots_enabled", sa.Boolean(),
                                                nullable=True, server_default=sa.false())),
    ("meeting_minutes", lambda: sa.Column("meeting_minutes", sa.Integer(), nullable=True)),
)


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for tabel in ("style_profiles", "mailbox_settings"):
        bestaand = _columns(tabel)
        for naam, maak in _KOLOMMEN:
            if naam not in bestaand:
                with op.batch_alter_table(tabel) as batch:
                    batch.add_column(maak())


def downgrade():
    for tabel in ("style_profiles", "mailbox_settings"):
        bestaand = _columns(tabel)
        for naam, _maak in _KOLOMMEN:
            if naam in bestaand:
                with op.batch_alter_table(tabel) as batch:
                    batch.drop_column(naam)

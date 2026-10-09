"""Hernoem het plan 'Starter' naar 'Personal' (prijswijziging sep 2026).

De plannaam 'Starter' verdwijnt: het gewone-mailbox-plan heet voortaan 'Personal'.
Deze data-migratie zet bestaande licenties en billing-rijen mee om, zodat de
entitlement-gating (die op de exacte plannaam matcht) blijft kloppen. Odoo blijft
de biller; de nieuwe listprijs (€12) staat in de app-code, niet in de DB.

Revision ID: rename_starter_personal
Revises: crm_directory_iso
Create Date: 2026-09-03

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "rename_starter_personal"
down_revision = "crm_directory_iso"
branch_labels = None
depends_on = None


def _has_table(table):
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _rename_plan(old, new):
    for table in ("licenses", "billing"):
        if _has_table(table):
            op.execute(
                sa.text(f"UPDATE {table} SET plan = :new WHERE plan = :old").bindparams(
                    new=new, old=old
                )
            )


def upgrade():
    _rename_plan("Starter", "Personal")


def downgrade():
    _rename_plan("Personal", "Starter")

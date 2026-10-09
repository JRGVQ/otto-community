"""volledig gebruikersprofiel — mobiel, afdeling en locatie op users

Revision ID: user_profile_v2_iso
Revises: sig_builder_iso
Create Date: 2026-08-23

Voedt de handtekening-placeholders ({{mobiel}}, {{afdeling}}, {{locatie}}) en wordt
automatisch aangevuld vanuit het Microsoft 365-profiel bij het koppelen.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "user_profile_v2_iso"
down_revision = "sig_builder_iso"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


_NEW = {
    "mobile": sa.String(length=64),
    "department": sa.String(length=128),
    "office_location": sa.String(length=128),
}


def upgrade():
    cols = _columns("users")
    missing = {k: v for k, v in _NEW.items() if k not in cols}
    if not missing:
        return
    with op.batch_alter_table("users") as batch:
        for name, type_ in missing.items():
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade():
    cols = _columns("users")
    present = [k for k in _NEW if k in cols]
    if not present:
        return
    with op.batch_alter_table("users") as batch:
        for name in present:
            batch.drop_column(name)

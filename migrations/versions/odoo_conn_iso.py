"""Eigen Odoo-koppeling per organisatie — voor CRM-logging (fase 6.5b)

Revision ID: odoo_conn_iso
Revises: staged_appt_iso
Create Date: 2026-08-24

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "odoo_conn_iso"
down_revision = "staged_appt_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("odoo_connections"):
        op.create_table(
            "odoo_connections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("base_url", sa.String(length=255), nullable=True),
            sa.Column("db_name", sa.String(length=128), nullable=True),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("api_key_enc", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("log_incoming", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("odoo_connections") as batch:
            batch.create_index("ix_odoo_connections_organization_id", ["organization_id"], unique=True)


def downgrade():
    if _has_table("odoo_connections"):
        op.drop_table("odoo_connections")

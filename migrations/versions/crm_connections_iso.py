"""Generieke CRM-koppelingen per organisatie — Odoo, HubSpot, Pipedrive, webhook.

Revision ID: crm_connections_iso
Revises: sig_variables_iso
Create Date: 2026-08-24

Vervangt het Odoo-specifieke `odoo_connections`: één tabel met een provider-kolom,
maximaal één koppeling per (organisatie, provider). Bestaande Odoo-koppelingen
verhuizen mee (provider='odoo'), daarna verdwijnt de oude tabel.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "crm_connections_iso"
down_revision = "sig_variables_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("crm_connections"):
        op.create_table(
            "crm_connections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
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
            sa.UniqueConstraint("organization_id", "provider", name="uq_crm_conn_org_provider"),
        )
        with op.batch_alter_table("crm_connections") as batch:
            batch.create_index("ix_crm_connections_organization_id", ["organization_id"])
    if _has_table("odoo_connections"):
        op.execute(
            "INSERT INTO crm_connections (organization_id, provider, base_url, db_name, "
            "username, api_key_enc, enabled, log_incoming, created_at, updated_at) "
            "SELECT organization_id, 'odoo', base_url, db_name, username, api_key_enc, "
            "enabled, log_incoming, created_at, updated_at FROM odoo_connections"
        )
        op.drop_table("odoo_connections")


def downgrade():
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
    if _has_table("crm_connections"):
        op.execute(
            "INSERT INTO odoo_connections (organization_id, base_url, db_name, username, "
            "api_key_enc, enabled, log_incoming, created_at, updated_at) "
            "SELECT organization_id, base_url, db_name, username, api_key_enc, enabled, "
            "log_incoming, created_at, updated_at FROM crm_connections WHERE provider = 'odoo'"
        )
        op.drop_table("crm_connections")

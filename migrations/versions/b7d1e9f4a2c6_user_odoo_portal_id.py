"""per-user Odoo portal login id (+ merge the open migration heads)

Adds users.odoo_portal_user_id so every teammate — not just the owner — gets their own
Odoo portal login (a person-contact under the company) to see the company's invoices +
helpdesk tickets. Also merges the three open heads into one so `flask db upgrade` is clean.

Revision ID: b7d1e9f4a2c6
Revises: msg_provider_thread_id_iso, d7c8e9f0a1b2, f3a4b5c6d7e8
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d1e9f4a2c6"
down_revision = ("msg_provider_thread_id_iso", "d7c8e9f0a1b2", "f3a4b5c6d7e8")
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("odoo_portal_user_id", sa.String(length=32), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("odoo_portal_user_id")

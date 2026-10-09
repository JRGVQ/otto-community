"""org Odoo portal-user link (odoo_portal_user_id)

Revision ID: f2a3b4c5d6e1
Revises: per_org_roles_iso
Create Date: 2026-06-29 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# Re-id'd from b8c9d0e1f2a3 (which collided with the merged To-Do sync migration) and
# chained onto the per-org-roles migration so the branch resolves to a single head.
revision = 'f2a3b4c5d6e1'
down_revision = 'per_org_roles_iso'
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c['name'] == column for c in insp.get_columns(table))


def upgrade():
    # Idempotent: an earlier re-id'd/duplicated copy of this migration (the rival
    # c0d1e2f3a4b5 org_odoo migration from PR #33) already added this column on some
    # databases, so a raw add_column hits psycopg DuplicateColumn. Only add if missing.
    if not _has_column('organizations', 'odoo_portal_user_id'):
        with op.batch_alter_table('organizations', schema=None) as batch_op:
            batch_op.add_column(sa.Column('odoo_portal_user_id', sa.String(length=32), nullable=True))


def downgrade():
    if _has_column('organizations', 'odoo_portal_user_id'):
        with op.batch_alter_table('organizations', schema=None) as batch_op:
            batch_op.drop_column('odoo_portal_user_id')

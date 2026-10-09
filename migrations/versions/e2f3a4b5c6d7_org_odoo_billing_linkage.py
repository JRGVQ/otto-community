"""org Odoo billing linkage (drop Stripe customer/subscription, add Odoo partner/subscription)

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4e5f6
Create Date: 2026-06-28 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f3a4b5c6d7'
down_revision = 'd1a2b3c4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('odoo_partner_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('odoo_subscription_id', sa.String(length=32), nullable=True))
        batch_op.drop_column('stripe_customer_id')
        batch_op.drop_column('stripe_subscription_id')


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stripe_subscription_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('stripe_customer_id', sa.String(length=64), nullable=True))
        batch_op.drop_column('odoo_subscription_id')
        batch_op.drop_column('odoo_partner_id')

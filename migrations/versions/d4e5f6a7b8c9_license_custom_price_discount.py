"""license custom price + discount

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-06-26 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('licenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('custom_price_eur', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('discount_pct', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('licenses', schema=None) as batch_op:
        batch_op.drop_column('discount_pct')
        batch_op.drop_column('custom_price_eur')

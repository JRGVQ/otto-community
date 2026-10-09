"""email log: capture failure reason

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a8
Create Date: 2026-06-28 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('email_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('email_logs', schema=None) as batch_op:
        batch_op.drop_column('error')

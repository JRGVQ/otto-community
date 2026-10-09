"""task planner linkage: assignee + plan/bucket

Revision ID: a7b8c9d0e1f2
Revises: 4dcc73653a02
Create Date: 2026-06-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = '4dcc73653a02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('plan_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('bucket_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('bucket_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('assignee_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('assignee_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('assignee_email', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('assignee_email')
        batch_op.drop_column('assignee_name')
        batch_op.drop_column('assignee_id')
        batch_op.drop_column('bucket_name')
        batch_op.drop_column('bucket_id')
        batch_op.drop_column('plan_name')
        batch_op.drop_column('plan_id')

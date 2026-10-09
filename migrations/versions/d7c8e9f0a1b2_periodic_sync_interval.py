"""periodic mailbox sync: per-account interval + scheduler leader lock

Revision ID: d7c8e9f0a1b2
Revises: c3d4e5f6a7b8
Create Date: 2026-06-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7c8e9f0a1b2'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_sync_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('sync_interval_seconds', sa.Integer(), nullable=True))

    op.create_table(
        'scheduler_locks',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('holder', sa.String(length=96), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade():
    op.drop_table('scheduler_locks')
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.drop_column('sync_interval_seconds')
        batch_op.drop_column('last_sync_at')

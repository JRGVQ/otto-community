"""teams fase 4: message assignment + state

Adds Message.assignee_user_id / Message.state (collaboration). The task-side
To Do linkage (provider ids/etag/lists) comes from b8c9d0e1f2a3_todo_two_way_sync.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f0a1b2c3d4e5'
down_revision = 'e9f0a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('assignee_user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('state', sa.String(length=16), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_messages_assignee_user_id_users'), 'users', ['assignee_user_id'], ['id'])


def downgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_messages_assignee_user_id_users'), type_='foreignkey')
        batch_op.drop_column('state')
        batch_op.drop_column('assignee_user_id')

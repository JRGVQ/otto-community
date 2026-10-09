"""tasks <-> Microsoft To Do two-way sync: lists, steps, rich task fields

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-29 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'todo_lists',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=True),
        sa.Column('provider_list_id', sa.String(length=255), nullable=True),
        sa.Column('delta_link', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'task_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('done', sa.Boolean(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=True),
        sa.Column('provider_step_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('reminder_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('recurrence', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=16), nullable=True, server_default='notStarted'))
        batch_op.add_column(sa.Column('important', sa.Boolean(), nullable=True, server_default=sa.false()))
        batch_op.add_column(sa.Column('provider_kind', sa.String(length=16), nullable=True, server_default='local'))
        batch_op.add_column(sa.Column('provider_etag', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('provider_last_modified', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('todo_list_id', sa.Integer(), nullable=True))
        batch_op.alter_column('list_name', existing_type=sa.String(length=32), type_=sa.String(length=128))
        batch_op.create_foreign_key('fk_tasks_todo_list', 'todo_lists', ['todo_list_id'], ['id'])


def downgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tasks_todo_list', type_='foreignkey')
        batch_op.alter_column('list_name', existing_type=sa.String(length=128), type_=sa.String(length=32))
        batch_op.drop_column('todo_list_id')
        batch_op.drop_column('provider_last_modified')
        batch_op.drop_column('provider_etag')
        batch_op.drop_column('provider_kind')
        batch_op.drop_column('important')
        batch_op.drop_column('status')
        batch_op.drop_column('recurrence')
        batch_op.drop_column('reminder_at')
        batch_op.drop_column('notes')

    op.drop_table('task_steps')
    op.drop_table('todo_lists')

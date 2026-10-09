"""email templates + send log

Revision ID: b2c3d4e5f6a8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_templates')),
    )
    with op.batch_alter_table('email_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_templates_key'), ['key'], unique=True)

    op.create_table(
        'email_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('to_addr', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('template_key', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_logs')),
    )
    with op.batch_alter_table('email_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_logs_to_addr'), ['to_addr'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_logs_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('email_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_email_logs_to_addr'))
    op.drop_table('email_logs')
    with op.batch_alter_table('email_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_templates_key'))
    op.drop_table('email_templates')

"""editable site content blocks

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-26 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'site_content',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_site_content')),
    )
    with op.batch_alter_table('site_content', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_site_content_key'), ['key'], unique=True)


def downgrade():
    with op.batch_alter_table('site_content', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_site_content_key'))
    op.drop_table('site_content')

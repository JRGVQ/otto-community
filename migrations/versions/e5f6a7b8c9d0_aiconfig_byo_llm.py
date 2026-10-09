"""aiconfig bring-your-own LLM fields

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-26 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ai_configs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('byo_provider', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('byo_api_key_enc', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('byo_model', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('byo_base_url', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('ai_configs', schema=None) as batch_op:
        batch_op.drop_column('byo_base_url')
        batch_op.drop_column('byo_model')
        batch_op.drop_column('byo_api_key_enc')
        batch_op.drop_column('byo_provider')

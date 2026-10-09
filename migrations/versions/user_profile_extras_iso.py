"""users: job_title + phone (profile extras, gevuld via M365 /me of handmatig)

Feeds the invite flow ("de rest halen we uit Microsoft 365") and, later on, the
signature placeholders ({{functie}}, {{telefoon}}).

Revision ID: user_profile_extras_iso
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'user_profile_extras_iso'
down_revision = '1a2b3c4d5e6f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('job_title', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('phone', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('phone')
        batch_op.drop_column('job_title')

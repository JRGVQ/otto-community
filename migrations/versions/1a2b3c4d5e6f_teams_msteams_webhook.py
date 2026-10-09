"""teams fase 6 (start): Microsoft Teams Incoming Webhook per mailbox

Adds mailbox_settings.teams_webhook_url + .teams_notify so a shared mailbox can post
events (new mail to answer, assignments) to a Microsoft Teams channel.

Revision ID: 1a2b3c4d5e6f
Revises: f0a1b2c3d4e5
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mailbox_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('teams_webhook_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('teams_notify', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('mailbox_settings', schema=None) as batch_op:
        batch_op.drop_column('teams_notify')
        batch_op.drop_column('teams_webhook_url')

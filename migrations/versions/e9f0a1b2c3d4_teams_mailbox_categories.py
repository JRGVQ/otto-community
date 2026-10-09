"""teams fase 3: per-mailbox category config

Adds mailbox_categories: a shared mailbox can enable a subset of the org's
categories and override their draft behaviour + AI instruction.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9f0a1b2c3d4'
down_revision = 'd8e9f0a1b2c3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mailbox_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('mail_account_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('draft_enabled', sa.Boolean(), nullable=True),
        sa.Column('ai_override', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['mail_account_id'], ['mail_accounts.id'], name=op.f('fk_mailbox_categories_mail_account_id_mail_accounts')),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_mailbox_categories_category_id_categories')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mailbox_categories')),
        sa.UniqueConstraint('mail_account_id', 'category_id', name='uq_mailbox_category'),
    )


def downgrade():
    op.drop_table('mailbox_categories')

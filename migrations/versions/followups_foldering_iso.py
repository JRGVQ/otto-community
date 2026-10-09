"""follow-ups, concept-TTL, guided-reply & auto-foldering settings + follow_ups table

Adds the automation prefs to style_profiles (personal) and mailbox_settings (shared):
follow_up_enabled/days, draft_ttl_days, auto_folder_enabled. Adds per-category
keep_in_inbox + target_folder for auto-foldering. Creates the follow_ups table backing
the Opvolgingen page.

Revision ID: followups_foldering_iso
Revises: user_profile_extras_iso
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'followups_foldering_iso'
down_revision = 'user_profile_extras_iso'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('style_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('follow_up_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
        batch_op.add_column(sa.Column('follow_up_days', sa.Integer(), nullable=True, server_default='3'))
        batch_op.add_column(sa.Column('draft_ttl_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('auto_folder_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))

    with op.batch_alter_table('mailbox_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('follow_up_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))
        batch_op.add_column(sa.Column('follow_up_days', sa.Integer(), nullable=True, server_default='3'))
        batch_op.add_column(sa.Column('draft_ttl_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('auto_folder_enabled', sa.Boolean(), nullable=True, server_default=sa.false()))

    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('keep_in_inbox', sa.Boolean(), nullable=True, server_default=sa.true()))
        batch_op.add_column(sa.Column('target_folder', sa.String(length=128), nullable=True))

    # Per-mailbox foldering override (NULL = inherit the org category).
    with op.batch_alter_table('mailbox_categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('keep_in_inbox', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('target_folder', sa.String(length=128), nullable=True))

    # When we replied — anchors the "wacht op antwoord" status + follow-up sweep.
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('replied_at', sa.DateTime(), nullable=True))

    op.create_table(
        'follow_ups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('provider_thread_id', sa.String(length=255), nullable=True),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.String(length=512), nullable=True),
        sa.Column('to_recipients', sa.String(length=512), nullable=True),
        sa.Column('last_sent_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('current_text', sa.Text(), nullable=True),
        sa.Column('provider_draft_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('follow_ups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_follow_ups_provider_thread_id'), ['provider_thread_id'], unique=False)


def downgrade():
    with op.batch_alter_table('follow_ups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_follow_ups_provider_thread_id'))
    op.drop_table('follow_ups')

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('replied_at')

    with op.batch_alter_table('mailbox_categories', schema=None) as batch_op:
        batch_op.drop_column('target_folder')
        batch_op.drop_column('keep_in_inbox')

    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_column('target_folder')
        batch_op.drop_column('keep_in_inbox')

    with op.batch_alter_table('mailbox_settings', schema=None) as batch_op:
        batch_op.drop_column('auto_folder_enabled')
        batch_op.drop_column('draft_ttl_days')
        batch_op.drop_column('follow_up_days')
        batch_op.drop_column('follow_up_enabled')

    with op.batch_alter_table('style_profiles', schema=None) as batch_op:
        batch_op.drop_column('auto_folder_enabled')
        batch_op.drop_column('draft_ttl_days')
        batch_op.drop_column('follow_up_days')
        batch_op.drop_column('follow_up_enabled')

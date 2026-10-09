"""teams: per-mailbox settings + mailbox member roles

Fase 1 of the Teams environment. Adds:
  - mailbox_settings: 1:1 per-mailbox config for shared mailboxes (voice,
    signature, auto-draft, knowledge source, SLA/office-hours, approval).
  - mailbox_members.can_send / .role: per-mailbox role and send permission.

Revision ID: d8e9f0a1b2c3
Revises: email_campaigns_iso
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8e9f0a1b2c3'
down_revision = 'email_campaigns_iso'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mailbox_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('mail_account_id', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=True),
        sa.Column('color', sa.String(length=9), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('auto_draft', sa.Boolean(), nullable=True),
        sa.Column('tone', sa.String(length=32), nullable=True),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('default_reply', sa.Text(), nullable=True),
        sa.Column('language', sa.String(length=48), nullable=True),
        sa.Column('knowledge_source_id', sa.Integer(), nullable=True),
        sa.Column('office_hours', sa.JSON(), nullable=True),
        sa.Column('sla_minutes', sa.Integer(), nullable=True),
        sa.Column('approval_required', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['mail_account_id'], ['mail_accounts.id'], name=op.f('fk_mailbox_settings_mail_account_id_mail_accounts')),
        sa.ForeignKeyConstraint(['knowledge_source_id'], ['knowledge_sources.id'], name=op.f('fk_mailbox_settings_knowledge_source_id_knowledge_sources')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mailbox_settings')),
        sa.UniqueConstraint('mail_account_id', name=op.f('uq_mailbox_settings_mail_account_id')),
    )

    with op.batch_alter_table('mailbox_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('can_send', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('role', sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table('mailbox_members', schema=None) as batch_op:
        batch_op.drop_column('role')
        batch_op.drop_column('can_send')

    op.drop_table('mailbox_settings')

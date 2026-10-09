"""shared mailboxes: members, notes, mailaccount fields

Revision ID: b1f2c3d4e5a6
Revises: 2909d96e5752
Create Date: 2026-06-26 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1f2c3d4e5a6'
down_revision = '2909d96e5752'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mailbox_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mail_account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('can_draft', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['mail_account_id'], ['mail_accounts.id'], name=op.f('fk_mailbox_members_mail_account_id_mail_accounts')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_mailbox_members_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mailbox_members')),
        sa.UniqueConstraint('mail_account_id', 'user_id', name='uq_mailbox_member'),
    )
    op.create_table(
        'message_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('author_user_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name=op.f('fk_message_notes_message_id_messages')),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_message_notes_organization_id_organizations')),
        sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], name=op.f('fk_message_notes_author_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_message_notes')),
    )
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('mailbox_type', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('target_address', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('delegated_via_account_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f('fk_mail_accounts_organization_id_organizations'),
            'organizations', ['organization_id'], ['id'])
        batch_op.create_foreign_key(
            op.f('fk_mail_accounts_delegated_via_account_id_mail_accounts'),
            'mail_accounts', ['delegated_via_account_id'], ['id'])

    # Backfill existing rows: every current mailbox is personal, and its org is the
    # connecting user's org. Keeps the NULL-safe visibility query correct.
    op.execute("UPDATE mail_accounts SET mailbox_type='personal' WHERE mailbox_type IS NULL")
    op.execute(
        "UPDATE mail_accounts SET organization_id = "
        "(SELECT users.organization_id FROM users WHERE users.id = mail_accounts.user_id) "
        "WHERE organization_id IS NULL"
    )


def downgrade():
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('fk_mail_accounts_delegated_via_account_id_mail_accounts'), type_='foreignkey')
        batch_op.drop_constraint(op.f('fk_mail_accounts_organization_id_organizations'), type_='foreignkey')
        batch_op.drop_column('delegated_via_account_id')
        batch_op.drop_column('target_address')
        batch_op.drop_column('mailbox_type')
        batch_op.drop_column('organization_id')
    op.drop_table('message_notes')
    op.drop_table('mailbox_members')

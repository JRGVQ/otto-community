"""org billing identity (B2B/B2C, VAT, address) for invoices + Peppol

Revision ID: d1a2b3c4e5f6
Revises: c3d4e5f6a7b8
Create Date: 2026-06-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1a2b3c4e5f6'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('billing_entity_type', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('billing_legal_name', sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column('billing_vat_number', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('billing_address_line1', sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column('billing_address_line2', sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column('billing_postal_code', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('billing_city', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('billing_country', sa.String(length=2), nullable=True))
    # Existing rows: default to "company" so current tenants keep B2B behaviour.
    op.execute("UPDATE organizations SET billing_entity_type = 'company' WHERE billing_entity_type IS NULL")


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('billing_country')
        batch_op.drop_column('billing_city')
        batch_op.drop_column('billing_postal_code')
        batch_op.drop_column('billing_address_line2')
        batch_op.drop_column('billing_address_line1')
        batch_op.drop_column('billing_vat_number')
        batch_op.drop_column('billing_legal_name')
        batch_op.drop_column('billing_entity_type')

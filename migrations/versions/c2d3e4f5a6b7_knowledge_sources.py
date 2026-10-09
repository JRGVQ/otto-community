"""knowledge_sources (SharePoint RAG)

Revision ID: c2d3e4f5a6b7
Revises: b1f2c3d4e5a6
Create Date: 2026-06-26 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'b1f2c3d4e5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'knowledge_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=24), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=True),
        sa.Column('site_url', sa.String(length=512), nullable=True),
        sa.Column('library_path', sa.String(length=512), nullable=True),
        sa.Column('site_id', sa.String(length=255), nullable=True),
        sa.Column('drive_id', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_knowledge_sources_organization_id_organizations')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_sources')),
    )


def downgrade():
    op.drop_table('knowledge_sources')

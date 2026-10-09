"""organizations.outlook_category_numbering — categorieën genummerd naar Outlook

Revision ID: outlook_cat_num_iso
Revises: sso_saml_iso
Create Date: 2026-08-21

Outlook sorteert categorieën alfabetisch en kent geen eigen volgorde. Met een nummer
ervoor ("1. Te beantwoorden") sorteert de belangrijkste bovenaan. Standaard uit, zodat
bestaande mailboxen hun huidige categorienamen houden.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "outlook_cat_num_iso"
down_revision = "sso_saml_iso"
branch_labels = None
depends_on = None


def _has_column(table, column):
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _has_column("organizations", "outlook_category_numbering"):
        with op.batch_alter_table("organizations") as batch:
            batch.add_column(sa.Column("outlook_category_numbering", sa.Boolean(),
                                       nullable=False, server_default="0"))


def downgrade():
    if _has_column("organizations", "outlook_category_numbering"):
        with op.batch_alter_table("organizations") as batch:
            batch.drop_column("outlook_category_numbering")

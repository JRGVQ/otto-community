"""eigen handtekening-variabelen (org-breed + persoonlijk) — én merge van twee heads

Revision ID: sig_variables_iso
Revises: msg_classified_by_iso, transcripts_iso
Create Date: 2026-08-24

Twee tabellen: signature_variables (definities; user_id NULL = org-breed, anders een
persoonlijke variabele) en signature_variable_values (ingevulde waarde per gebruiker).
De tuple-down_revision voegt de twee parallelle migratieketens (#105/#106) samen
tot één head. De losse lege merge-revisies die hetzelfde deden zijn verwijderd
(PR #110) — dit is sindsdien hét samenvoegpunt van de graph.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_variables_iso"
down_revision = ("msg_classified_by_iso", "transcripts_iso")
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("signature_variables"):
        op.create_table(
            "signature_variables",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "key", name="uq_sig_var_org_key"),
        )
        with op.batch_alter_table("signature_variables") as batch:
            batch.create_index("ix_signature_variables_organization_id", ["organization_id"])

    if not _has_table("signature_variable_values"):
        op.create_table(
            "signature_variable_values",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("variable_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("value", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["variable_id"], ["signature_variables.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("variable_id", "user_id", name="uq_sig_var_value"),
        )
        with op.batch_alter_table("signature_variable_values") as batch:
            batch.create_index("ix_signature_variable_values_organization_id", ["organization_id"])
            batch.create_index("ix_signature_variable_values_variable_id", ["variable_id"])
            batch.create_index("ix_signature_variable_values_user_id", ["user_id"])


def downgrade():
    if _has_table("signature_variable_values"):
        op.drop_table("signature_variable_values")
    if _has_table("signature_variables"):
        op.drop_table("signature_variables")

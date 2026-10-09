"""regel-gebaseerde handtekening-toewijzing (verzender/ontvanger-voorwaarden)

Revision ID: sig_rules_iso
Revises: sig_assets_photos_iso
Create Date: 2026-09-03

Fase 3 van de handtekening-uitbreiding: signature_rules (regel → handtekening, met
priority + enabled) en signature_rule_conditions (voorwaarden, AND per regel). Backward-
compatible: bestaande SignatureAssignments blijven werken; nul regels = het oude gedrag.

Idempotent met _has_table-guards; hangt aan de fase-2-head, single head blijft behouden.
Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_rules_iso"
down_revision = "sig_assets_photos_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("signature_rules"):
        op.create_table(
            "signature_rules",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("signature_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("signature_rules") as batch:
            batch.create_index("ix_signature_rules_organization_id", ["organization_id"])
            batch.create_index("ix_signature_rules_signature_id", ["signature_id"])

    if not _has_table("signature_rule_conditions"):
        op.create_table(
            "signature_rule_conditions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=False),
            sa.Column("field", sa.String(length=32), nullable=False),
            sa.Column("op", sa.String(length=16), nullable=False, server_default="equals"),
            sa.Column("value", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["rule_id"], ["signature_rules.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("signature_rule_conditions") as batch:
            batch.create_index("ix_signature_rule_conditions_organization_id", ["organization_id"])
            batch.create_index("ix_signature_rule_conditions_rule_id", ["rule_id"])


def downgrade():
    if _has_table("signature_rule_conditions"):
        op.drop_table("signature_rule_conditions")
    if _has_table("signature_rules"):
        op.drop_table("signature_rules")

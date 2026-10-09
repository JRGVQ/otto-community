"""centraal handtekening-beheer: gebruiker-voorstellen met IT-goedkeuring

Revision ID: sig_central_iso
Revises: cloud_sig_iso
Create Date: 2026-09-07

Voegt centraal beheer toe: een org-schakelaar (cloud_signature_settings.central_management)
en signature_change_requests (door een gebruiker voorgestelde wijzigingen aan zijn eigen
handtekening-velden, wachtend op goedkeuring door IT).

Idempotent met _has_table/_has_column-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_central_iso"
down_revision = "cloud_sig_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if _has_table("cloud_signature_settings") and not _has_column("cloud_signature_settings", "central_management"):
        with op.batch_alter_table("cloud_signature_settings") as batch:
            batch.add_column(sa.Column("central_management", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))

    if not _has_table("signature_change_requests"):
        op.create_table(
            "signature_change_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("signature_change_requests") as batch:
            batch.create_index("ix_signature_change_requests_organization_id", ["organization_id"])
            batch.create_index("ix_signature_change_requests_user_id", ["user_id"])


def downgrade():
    if _has_table("signature_change_requests"):
        op.drop_table("signature_change_requests")
    if _has_column("cloud_signature_settings", "central_management"):
        with op.batch_alter_table("cloud_signature_settings") as batch:
            batch.drop_column("central_management")

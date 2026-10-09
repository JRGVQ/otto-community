"""handtekening-builder + HTML-inbox — html-kolom, toewijzingen, groepscache, body_html

Revision ID: sig_builder_iso
Revises: propose_slots_iso
Create Date: 2026-08-23

Vier samenhangende wijzigingen van dezelfde feature-batch:
  1. signatures.html — de rijke (HTML) versie uit de handtekening-builder;
  2. signature_assignments — expliciete toewijzing per gebruiker of M365-groep;
  3. mail_accounts.graph_group_ids/_synced_at — cache van Graph-groepslidmaatschappen;
  4. messages.body_html — de originele HTML-body voor de inbox-weergave.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_builder_iso"
down_revision = "propose_slots_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "html" not in _columns("signatures"):
        with op.batch_alter_table("signatures") as batch:
            batch.add_column(sa.Column("html", sa.Text(), nullable=True))

    cols = _columns("mail_accounts")
    if "graph_group_ids" not in cols or "graph_groups_synced_at" not in cols:
        with op.batch_alter_table("mail_accounts") as batch:
            if "graph_group_ids" not in cols:
                batch.add_column(sa.Column("graph_group_ids", sa.JSON(), nullable=True))
            if "graph_groups_synced_at" not in cols:
                batch.add_column(sa.Column("graph_groups_synced_at", sa.DateTime(), nullable=True))

    if "body_html" not in _columns("messages"):
        with op.batch_alter_table("messages") as batch:
            batch.add_column(sa.Column("body_html", sa.Text(), nullable=True))

    if not _has_table("signature_assignments"):
        op.create_table(
            "signature_assignments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("signature_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("value", sa.String(length=255), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "kind", "value", name="uq_sig_assign_org_kind_value"),
        )
        with op.batch_alter_table("signature_assignments") as batch:
            batch.create_index("ix_signature_assignments_organization_id", ["organization_id"])
            batch.create_index("ix_signature_assignments_signature_id", ["signature_id"])


def downgrade():
    if _has_table("signature_assignments"):
        op.drop_table("signature_assignments")
    if "body_html" in _columns("messages"):
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("body_html")
    cols = _columns("mail_accounts")
    with op.batch_alter_table("mail_accounts") as batch:
        if "graph_groups_synced_at" in cols:
            batch.drop_column("graph_groups_synced_at")
        if "graph_group_ids" in cols:
            batch.drop_column("graph_group_ids")
    if "html" in _columns("signatures"):
        with op.batch_alter_table("signatures") as batch:
            batch.drop_column("html")

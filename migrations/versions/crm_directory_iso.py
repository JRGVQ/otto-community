"""Otto's eigen CRM: bedrijven, contacten en projecten (Teams/Enterprise)

Revision ID: crm_directory_iso
Revises: crm_connections_iso
Create Date: 2026-09-02

Vijf tabellen: crm_companies (domein + standaardcategorie), crm_contacts (e-mail +
standaardcategorie, passief opgebouwd), crm_projects, en de koppeltabellen
crm_project_contacts en crm_project_messages.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "crm_directory_iso"
down_revision = "crm_connections_iso"
branch_labels = None
depends_on = None


def _has(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has("crm_companies"):
        op.create_table(
            "crm_companies",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("default_category_id", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["default_category_id"], ["categories.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "domain", name="uq_crm_company_org_domain"),
        )
        with op.batch_alter_table("crm_companies") as b:
            b.create_index("ix_crm_companies_organization_id", ["organization_id"])

    if not _has("crm_contacts"):
        op.create_table(
            "crm_contacts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("company_id", sa.Integer(), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("default_category_id", sa.Integer(), nullable=True),
            sa.Column("message_count", sa.Integer(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["company_id"], ["crm_companies.id"]),
            sa.ForeignKeyConstraint(["default_category_id"], ["categories.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "email", name="uq_crm_contact_org_email"),
        )
        with op.batch_alter_table("crm_contacts") as b:
            b.create_index("ix_crm_contacts_organization_id", ["organization_id"])
            b.create_index("ix_crm_contacts_company_id", ["company_id"])
            b.create_index("ix_crm_contacts_email", ["email"])

    if not _has("crm_projects"):
        op.create_table(
            "crm_projects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("color", sa.String(length=9), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("crm_projects") as b:
            b.create_index("ix_crm_projects_organization_id", ["organization_id"])

    if not _has("crm_project_contacts"):
        op.create_table(
            "crm_project_contacts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("contact_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["crm_projects.id"]),
            sa.ForeignKeyConstraint(["contact_id"], ["crm_contacts.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "contact_id", name="uq_crm_projcontact"),
        )
        with op.batch_alter_table("crm_project_contacts") as b:
            b.create_index("ix_crm_project_contacts_project_id", ["project_id"])
            b.create_index("ix_crm_project_contacts_contact_id", ["contact_id"])

    if not _has("crm_project_messages"):
        op.create_table(
            "crm_project_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["crm_projects.id"]),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "message_id", name="uq_crm_projmsg"),
        )
        with op.batch_alter_table("crm_project_messages") as b:
            b.create_index("ix_crm_project_messages_organization_id", ["organization_id"])
            b.create_index("ix_crm_project_messages_project_id", ["project_id"])
            b.create_index("ix_crm_project_messages_message_id", ["message_id"])


def downgrade():
    for t in ("crm_project_messages", "crm_project_contacts", "crm_projects",
              "crm_contacts", "crm_companies"):
        if _has(t):
            op.drop_table(t)

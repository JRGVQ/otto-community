"""community edition: drop the hosted-service tables and columns

Revision ID: community_edition_iso
Revises: hot_path_indexes_iso
Create Date: 2026-10-09

The self-hosted edition has no billing, plans, trials, waitlist, marketing site, owner
e-mail campaigns or vendor support tickets. This removes their tables and columns:

  * tables: billing, invoices, tickets, ticket_replies, waitlist_signups, site_content,
    email_campaigns, campaign_recipients
  * organizations: the ephemeral-demo flag, the Odoo linkage and the invoice identity
  * users: the Odoo portal id and the marketing opt-out
  * licenses: everything except the access status (active / suspended)

Idempotent: every drop is guarded on what actually exists, so it is safe on a fresh
database and on one that was already partly cleaned. One-way: the dropped data is not
restored on downgrade (the columns come back empty so older code can boot).
"""
from alembic import op
import sqlalchemy as sa

revision = "community_edition_iso"
down_revision = "hot_path_indexes_iso"
branch_labels = None
depends_on = None

# Children before parents, so foreign keys never block a drop.
_TABLES = [
    "ticket_replies", "tickets",
    "campaign_recipients", "email_campaigns",
    "invoices", "billing",
    "waitlist_signups", "site_content",
]

_COLUMNS = {
    "organizations": [
        "is_ephemeral",
        "odoo_partner_id", "odoo_subscription_id", "odoo_portal_user_id",
        "billing_entity_type", "billing_legal_name", "billing_vat_number",
        "billing_address_line1", "billing_address_line2", "billing_postal_code",
        "billing_city", "billing_country",
    ],
    "users": ["odoo_portal_user_id", "marketing_opt_out"],
    "licenses": [
        "plan", "seats", "expires_on", "override_until",
        "custom_price_eur", "discount_pct", "extra_ai_credit_eur",
        "mod_pro", "mod_teams", "mod_signatures", "sig_billing_mode",
    ],
}


def upgrade():
    insp = sa.inspect(op.get_bind())
    existing = set(insp.get_table_names())
    for table in _TABLES:
        if table in existing:
            op.drop_table(table)

    for table, columns in _COLUMNS.items():
        if table not in existing:
            continue
        present = {c["name"] for c in insp.get_columns(table)}
        to_drop = [c for c in columns if c in present]
        if not to_drop:
            continue
        with op.batch_alter_table(table) as batch:
            for column in to_drop:
                batch.drop_column(column)

    # Every organisation is simply active now: map the old trial/expired/cancelled states.
    if "licenses" in existing:
        op.execute("UPDATE licenses SET status = 'active' WHERE status IS NULL OR status <> 'suspended'")


def downgrade():
    insp = sa.inspect(op.get_bind())
    existing = set(insp.get_table_names())
    if "licenses" in existing:
        present = {c["name"] for c in insp.get_columns("licenses")}
        with op.batch_alter_table("licenses") as batch:
            if "plan" not in present:
                batch.add_column(sa.Column("plan", sa.String(32)))
            if "seats" not in present:
                batch.add_column(sa.Column("seats", sa.Integer()))
            if "expires_on" not in present:
                batch.add_column(sa.Column("expires_on", sa.Date()))
    if "users" in existing:
        present = {c["name"] for c in insp.get_columns("users")}
        if "marketing_opt_out" not in present:
            with op.batch_alter_table("users") as batch:
                batch.add_column(sa.Column("marketing_opt_out", sa.Boolean(), nullable=False,
                                           server_default=sa.false()))

"""afwezigheidsassistent (out-of-office) + handtekening-banners

Revision ID: sig_ar_banners_iso
Revises: sig_assets_photos_iso
Create Date: 2026-09-04

Fase 4 van de handtekening-uitbreiding: auto_reply_settings (centraal OOO per postvak,
gepusht naar Exchange mailboxSettings) en signature_banners (geplande banner boven/onder
de handtekening).

NB: hangt aan de fase-2-head (sig_assets_photos_iso), want deze branch is los van fase 3
(#119) opgezet. Landt fase 3 eerder op main, dan ontstaat er tijdelijk een tweede head en
moet deze migratie achter sig_rules_iso geketend worden (merge origin/main + herketen).

Idempotent met _has_table-guards. Revisie-id kort (<32 tekens).
"""
from alembic import op
import sqlalchemy as sa

revision = "sig_ar_banners_iso"
down_revision = "sig_rules_iso"
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table("auto_reply_settings"):
        op.create_table(
            "auto_reply_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("mail_account_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="disabled"),
            sa.Column("scheduled_start", sa.DateTime(), nullable=True),
            sa.Column("scheduled_end", sa.DateTime(), nullable=True),
            sa.Column("internal_reply_html", sa.Text(), nullable=True),
            sa.Column("external_reply_html", sa.Text(), nullable=True),
            sa.Column("external_audience", sa.String(length=16), nullable=True, server_default="all"),
            sa.Column("push_state", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("last_pushed_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["mail_account_id"], ["mail_accounts.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("mail_account_id", name="uq_autoreply_account"),
        )
        with op.batch_alter_table("auto_reply_settings") as batch:
            batch.create_index("ix_auto_reply_settings_organization_id", ["organization_id"])
            batch.create_index("ix_auto_reply_settings_mail_account_id", ["mail_account_id"])

    if not _has_table("signature_banners"):
        op.create_table(
            "signature_banners",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("image_url", sa.String(length=512), nullable=False),
            sa.Column("link_url", sa.String(length=512), nullable=True),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("placement", sa.String(length=8), nullable=False, server_default="below"),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("signature_banners") as batch:
            batch.create_index("ix_signature_banners_organization_id", ["organization_id"])


def downgrade():
    if _has_table("signature_banners"):
        op.drop_table("signature_banners")
    if _has_table("auto_reply_settings"):
        op.drop_table("auto_reply_settings")

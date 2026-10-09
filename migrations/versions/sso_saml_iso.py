"""sso_configs: SAML 2.0-velden naast de bestaande OIDC-koppeling

Revision ID: sso_saml_iso
Revises: snippets_iso
Create Date: 2026-08-21

Geen geheim hier: SAML-vertrouwen berust op het ondertekeningscertificaat van de IdP,
dat publiek is van opzet. Vandaar geen versleuteld veld zoals bij OIDC.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""
from alembic import op
import sqlalchemy as sa

revision = "sso_saml_iso"
down_revision = "snippets_iso"
branch_labels = None
depends_on = None

_COLUMNS = {
    "saml_entity_id": sa.String(length=255),
    "saml_sso_url": sa.String(length=512),
    "saml_x509_cert": sa.Text(),
}


def _existing():
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns("sso_configs")}


def upgrade():
    have = _existing()
    with op.batch_alter_table("sso_configs") as batch:
        for name, type_ in _COLUMNS.items():
            if name not in have:
                batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade():
    have = _existing()
    with op.batch_alter_table("sso_configs") as batch:
        for name in _COLUMNS:
            if name in have:
                batch.drop_column(name)

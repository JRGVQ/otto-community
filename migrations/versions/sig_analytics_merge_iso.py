"""merge: handtekening-builder-keten + analytics-layout-keten

Revision ID: sig_analytics_merge_iso
Revises: analytics_layout_iso, user_profile_v2_iso
Create Date: 2026-08-23

Twee parallelle takken (PR met analytics-layout en de handtekening-batch) samengevoegd
tot één head — zelfde patroon als 4dcc73653a02. Geen schemawijzigingen.

Revisie-id kort gehouden (<32 tekens) — alembic_version.version_num is VARCHAR(32).
"""

revision = "sig_analytics_merge_iso"
down_revision = ("analytics_layout_iso", "user_profile_v2_iso")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

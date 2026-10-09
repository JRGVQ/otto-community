"""indexen op de hete paden: messages, threads, drafts

Revision ID: hot_path_indexes_iso
Revises: waitlist_signups_iso
Create Date: 2026-09-15

`threads` en `drafts` hadden géén enkele index — ook niet op de foreign key
`drafts.message_id` (Postgres indexeert FK's niet automatisch). En `messages` was alleen
geïndexeerd op de provider-id's, niet op waar de inbox werkelijk op filtert.

Gevolg zonder deze indexen:
  * elke inbox-, briefing- en analyse-render doet een sequential scan over de héle
    messages-tabel (alle tenants);
  * `_get_or_create_thread` scant `threads` volledig voor ELK binnenkomend bericht
    tijdens de delta-sync;
  * `selectinload(Message.draft)` en `_thread_open_concept` scannen `drafts` volledig.

Puur additief: alleen indexen, geen kolom- of datawijziging. Idempotent via een guard op
de bestaande indexnamen, zodat hij veilig opnieuw kan draaien.
"""
from alembic import op
import sqlalchemy as sa

revision = "hot_path_indexes_iso"
down_revision = "waitlist_signups_iso"
branch_labels = None
depends_on = None

# (indexnaam, tabel, kolommen)
_INDEXES = [
    # De inbox filtert op organisatie + niet-gearchiveerd en sorteert op ontvangsttijd.
    ("ix_messages_org_archived_received", "messages", ["organization_id", "archived", "received_at"]),
    # Per postvak (Teams-weergave, reconciliatie, opschoning).
    ("ix_messages_account_id", "messages", ["account_id"]),
    # `_get_or_create_thread` zoekt precies op dit paar, per binnenkomend bericht.
    ("ix_threads_account_provider", "threads", ["account_id", "provider_thread_id"]),
    # Foreign key zonder index: elke concept-lookup scande de tabel.
    ("ix_drafts_message_id", "drafts", ["message_id"]),
    # `_thread_open_concept` en de opvolgsweep filteren op status.
    ("ix_drafts_status", "drafts", ["status"]),
    # Bestaande drift: het model declareerde index=True op roles.organization_id, maar
    # geen enkele migratie maakte hem aan. Meegenomen zodat autogenerate weer schoon is.
    ("ix_roles_organization_id", "roles", ["organization_id"]),
]


def _bestaat(insp, tabel, naam):
    if tabel not in insp.get_table_names():
        return True          # tabel weg → niets te doen
    return naam in {i["name"] for i in insp.get_indexes(tabel)}


def upgrade():
    insp = sa.inspect(op.get_bind())
    for naam, tabel, kolommen in _INDEXES:
        if not _bestaat(insp, tabel, naam):
            op.create_index(naam, tabel, kolommen)


def downgrade():
    insp = sa.inspect(op.get_bind())
    for naam, tabel, _kolommen in reversed(_INDEXES):
        if _bestaat(insp, tabel, naam) and tabel in insp.get_table_names():
            op.drop_index(naam, table_name=tabel)

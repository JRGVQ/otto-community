"""Het CRM van de klant zelf — voor het loggen van klantmail in hun eigen systeem.

Vervangt het Odoo-specifieke `odoo_connections`: elke organisatie kan per CRM-soort
(Odoo, HubSpot, Pipedrive, webhook) één koppeling hebben. De kolommen zijn de kleine
gemene deler; wat een provider niet gebruikt (bv. `db_name` buiten Odoo) blijft leeg.
De sleutel is van de klant en staat versleuteld opgeslagen, zoals alle secrets.

Let op het verschil met `app/odoo/_client.py`: dát is Otto's eigen Odoo (facturatie,
entitlements). Dit is het CRM van de KMO die Otto gebruikt.
"""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin
from app.security.crypto import decrypt, encrypt

# Welke velden een koppeling minimaal nodig heeft om te kunnen werken. Bij de
# webhook is het geheim optioneel (zonder geheim gewoon geen signature-header).
_REQUIRED_FIELDS = {
    "odoo": ("base_url", "db_name", "username", "api_key_enc"),
    "hubspot": ("api_key_enc",),
    "pipedrive": ("api_key_enc",),
    "webhook": ("base_url",),
}


class CrmConnection(TimestampMixin, db.Model):
    __tablename__ = "crm_connections"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "provider", name="uq_crm_conn_org_provider"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    provider = db.Column(db.String(32), nullable=False)  # odoo | hubspot | pipedrive | webhook

    base_url = db.Column(db.String(255))   # Odoo-URL of webhook-URL
    db_name = db.Column(db.String(128))    # alleen Odoo
    username = db.Column(db.String(255))   # alleen Odoo
    api_key_enc = db.Column(db.Text)       # versleuteld, nooit in klaartekst opgeslagen

    enabled = db.Column(db.Boolean, default=False)
    # Inkomende klantmail loggen bij het contact. Uitgaand loggen kan later; inkomend is
    # waar de winst zit (de klant reageert, jij weet meteen dat het in het CRM staat).
    log_incoming = db.Column(db.Boolean, default=True)

    @property
    def api_key(self) -> str | None:
        return decrypt(self.api_key_enc)

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        self.api_key_enc = encrypt(value) if value else None

    @property
    def is_ready(self) -> bool:
        required = _REQUIRED_FIELDS.get(self.provider or "", ())
        return bool(self.enabled and required
                    and all(getattr(self, field) for field in required))

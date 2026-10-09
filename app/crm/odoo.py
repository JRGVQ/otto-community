"""Odoo-connector — dunne schil rond `app/crm/odoo_client.py`, dat het echte werk doet.

Het loggen zelf (contact zoeken, notitie op de kaart én op de open deal) is niet
veranderd; deze klasse maakt Odoo alleen één van de standaard-CRM-koppelingen.
"""
from __future__ import annotations

from flask_babel import gettext as _

from app.crm.base import CRMConnector


class OdooConnector(CRMConnector):
    provider = "odoo"

    def log_mail(self, from_email, subject, body, direction="in") -> dict:
        from app.crm import odoo_client as tenant

        return tenant.log_mail(self.conn, from_email=from_email, subject=subject,
                               body=body, direction=direction)

    def test_connection(self) -> dict:
        from app.crm import odoo_client as tenant

        try:
            client = tenant.TenantOdoo(self.conn)
            # Authenticeert én leest: een sleutel zonder leesrecht valt hier al door de mand.
            n = client.exec("res.partner", "search_count", [])
        except Exception as exc:  # noqa: BLE001 — de echte reden hoort bij de testknop
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": _("Odoo verbonden — %(n)s contacten gevonden.", n=n)}

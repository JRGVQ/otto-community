"""HubSpot-connector — logt klantmail als notitie bij het bestaande contact.

Authenticatie via een private-app-token (Instellingen → Integraties → Private apps
in HubSpot; scopes `crm.objects.contacts.read` en `crm.objects.contacts.write`).
Zelfde grondregel als Odoo: geen contact met dat e-mailadres → niets loggen.
"""
from __future__ import annotations

from datetime import UTC, datetime

from flask import current_app
from flask_babel import gettext as _

from app.crm.base import CRMConnector, http_json, snippet_of

API = "https://api.hubapi.com"

# Vaste HubSpot-associatie "note → contact".
_NOTE_TO_CONTACT = 202


class HubSpotConnector(CRMConnector):
    provider = "hubspot"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.conn.api_key}"}

    def _find_contact(self, email: str) -> str | None:
        data = http_json("POST", f"{API}/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "email", "operator": "EQ", "value": email},
            ]}],
            "limit": 1,
        }, self._headers())
        results = data.get("results") or []
        return results[0].get("id") if results else None

    def log_mail(self, from_email, subject, body, direction="in") -> dict:
        email = (from_email or "").strip()
        if not (self.conn.is_ready and email):
            return {"logged": False, "reason": "not_ready"}
        try:
            contact_id = self._find_contact(email)
            if not contact_id:
                return {"logged": False, "reason": "no_contact"}
            pijl = "📥 Inkomende" if direction == "in" else "📤 Uitgaande"
            onderwerp = (subject or "(geen onderwerp)").strip()
            note = f"<p><b>{pijl} mail via Otto</b><br>{onderwerp}</p>"
            tekst = snippet_of(body)
            if tekst:
                note += f"<p>{tekst}</p>"
            http_json("POST", f"{API}/crm/v3/objects/notes", {
                "properties": {
                    "hs_timestamp": datetime.now(UTC).isoformat(),
                    "hs_note_body": note,
                },
                "associations": [{
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED",
                               "associationTypeId": _NOTE_TO_CONTACT}],
                }],
            }, self._headers())
            current_app.logger.info("[crm:hubspot] mail gelogd bij contact %s", contact_id)
            return {"logged": True, "contact_id": contact_id}
        except Exception as exc:  # noqa: BLE001 — het CRM van de klant mag Otto niet breken
            current_app.logger.warning("[crm:hubspot] loggen mislukt: %s", exc)
            return {"logged": False, "reason": "error"}

    def test_connection(self) -> dict:
        try:
            http_json("GET", f"{API}/crm/v3/objects/contacts?limit=1", None, self._headers())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": _("HubSpot verbonden — token en contactrechten in orde.")}

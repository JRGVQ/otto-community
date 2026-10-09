"""Pipedrive-connector — logt klantmail als notitie bij de bestaande persoon,
en hangt ze ook aan de open deal als die er is (zelfde gedrag als de Odoo-koppeling).

Authenticatie via het persoonlijke API-token (Pipedrive → Personal preferences →
API). Geen persoon met dat e-mailadres → niets loggen.
"""
from __future__ import annotations

import urllib.parse

from flask import current_app
from flask_babel import gettext as _

from app.crm.base import CRMConnector, http_json, snippet_of

API = "https://api.pipedrive.com/v1"


class PipedriveConnector(CRMConnector):
    provider = "pipedrive"

    def _url(self, path: str, **params) -> str:
        params["api_token"] = self.conn.api_key
        return f"{API}{path}?{urllib.parse.urlencode(params)}"

    def _find_person(self, email: str) -> int | None:
        data = http_json("GET", self._url("/persons/search", term=email,
                                          fields="email", exact_match="true", limit=1))
        items = (data.get("data") or {}).get("items") or []
        return items[0]["item"]["id"] if items else None

    def _find_open_deal(self, person_id: int) -> int | None:
        try:
            data = http_json("GET", self._url("/deals", person_id=person_id,
                                              status="open", limit=1))
            rows = data.get("data") or []
            return rows[0]["id"] if rows else None
        except Exception:  # noqa: BLE001 — geen deal(recht) is geen fout, gewoon geen deal
            return None

    def log_mail(self, from_email, subject, body, direction="in") -> dict:
        email = (from_email or "").strip()
        if not (self.conn.is_ready and email):
            return {"logged": False, "reason": "not_ready"}
        try:
            person_id = self._find_person(email)
            if not person_id:
                return {"logged": False, "reason": "no_contact"}
            pijl = "📥 Inkomende" if direction == "in" else "📤 Uitgaande"
            onderwerp = (subject or "(geen onderwerp)").strip()
            note = f"<b>{pijl} mail via Otto</b><br>{onderwerp}"
            tekst = snippet_of(body)
            if tekst:
                note += f"<br><br>{tekst}"
            deal_id = self._find_open_deal(person_id)
            payload = {"content": note, "person_id": person_id}
            if deal_id:
                payload["deal_id"] = deal_id
            http_json("POST", self._url("/notes"), payload)
            current_app.logger.info("[crm:pipedrive] mail gelogd bij persoon %s (deal %s)",
                                    person_id, deal_id)
            return {"logged": True, "person_id": person_id, "deal_id": deal_id}
        except Exception as exc:  # noqa: BLE001 — het CRM van de klant mag Otto niet breken
            current_app.logger.warning("[crm:pipedrive] loggen mislukt: %s", exc)
            return {"logged": False, "reason": "error"}

    def test_connection(self) -> dict:
        try:
            data = http_json("GET", self._url("/users/me"))
            if not data.get("success"):
                return {"ok": False, "detail": str(data.get("error") or "onbekende fout")}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}
        naam = ((data.get("data") or {}).get("name")) or "?"
        return {"ok": True, "detail": _("Pipedrive verbonden als %(name)s.", name=naam)}

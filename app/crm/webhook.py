"""Webhook-connector — de standaard-koppeling voor elk CRM dat we niet ingebouwd hebben.

Stuurt elke inkomende klantmail als één JSON-bericht naar een URL van de klant
(rechtstreeks naar hun eigen systeem, of via Zapier/Make/n8n door naar Salesforce,
Teamleader, noem maar op). Bewust géén contact-filter zoals bij de andere
connectors: Otto kent het CRM achter de webhook niet, dus het filteren en matchen
gebeurt aan de ontvangende kant.

Met een ingesteld geheim tekent Otto elk bericht met HMAC-SHA256 in de header
`X-Otto-Signature` (formaat `sha256=<hex>`, over de rauwe JSON-body), zodat de
ontvanger kan verifiëren dat het echt van Otto komt.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request

from flask import current_app
from flask_babel import gettext as _

from app.crm.base import CRMConnector, CRMError, HTTP_TIMEOUT, snippet_of


class WebhookConnector(CRMConnector):
    provider = "webhook"

    def _post(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "Otto-CRM-Webhook/1.0"}
        secret = self.conn.api_key
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Otto-Signature"] = f"sha256={sig}"
        req = urllib.request.Request(self.conn.base_url, data=body, method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):  # noqa: S310 — URL komt uit de eigen koppeling van de klant
                pass
        except urllib.error.HTTPError as exc:
            raise CRMError(f"HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise CRMError(str(exc)) from exc

    def log_mail(self, from_email, subject, body, direction="in") -> dict:
        if not self.conn.is_ready:
            return {"logged": False, "reason": "not_ready"}
        try:
            self._post({
                "event": "mail.received" if direction == "in" else "mail.sent",
                "from": (from_email or "").strip(),
                "subject": (subject or "").strip(),
                "snippet": snippet_of(body),
                "direction": direction,
            })
            return {"logged": True}
        except Exception as exc:  # noqa: BLE001 — de webhook van de klant mag Otto niet breken
            current_app.logger.warning("[crm:webhook] doorsturen mislukt: %s", exc)
            return {"logged": False, "reason": "error"}

    def test_connection(self) -> dict:
        try:
            self._post({"event": "ping"})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": _("Webhook bereikbaar — testbericht (event \"ping\") afgeleverd.")}

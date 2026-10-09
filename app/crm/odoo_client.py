"""Klantmail loggen in de eigen Odoo van een organisatie.

Werkt tegen de Odoo die de KMO zelf koppelt (`CrmConnection` met provider "odoo",
via `app/crm/odoo.py`), niet tegen Otto's eigen
Odoo. Bewust smal: we loggen alleen bij een contact dat al in hun CRM bestaat. Wie mailt en
niet in Odoo staat, is geen klant of lead — daar een contact voor aanmaken zou het CRM
vervuilen met ruis. Zo is de regel eenvoudig uit te leggen: mail van iemand die je kent,
komt op zijn kaart te staan, en op de open deal als die er is.

Alles is best-effort: een onbereikbare of misgeconfigureerde Odoo mag de mailverwerking
nooit breken. Geen contact gevonden is geen fout, gewoon niets te doen.
"""
from __future__ import annotations

import xmlrpc.client

from flask import current_app


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def _proxy(url: str, timeout: float):
    transport = (_TimeoutSafeTransport if url.lower().startswith("https") else _TimeoutTransport)(timeout)
    return xmlrpc.client.ServerProxy(url, transport=transport)


class TenantOdoo:
    """Een dunne XML-RPC-client, gebonden aan één gekoppelde Odoo."""

    def __init__(self, conn, timeout: float | None = None):
        self.base_url = (conn.base_url or "").rstrip("/")
        self.db_name = conn.db_name
        self.username = conn.username
        self.api_key = conn.api_key
        self.timeout = timeout if timeout is not None else current_app.config.get("CRM_TIMEOUT_SECONDS", 10)
        self._uid = None
        self._models = None

    def _connect(self):
        common = _proxy(f"{self.base_url}/xmlrpc/2/common", self.timeout)
        uid = common.authenticate(self.db_name, self.username, self.api_key, {})
        if not uid:
            raise RuntimeError("Odoo authentication failed")
        self._uid = uid
        self._models = _proxy(f"{self.base_url}/xmlrpc/2/object", self.timeout)

    def exec(self, model: str, method: str, *args, **kw):
        if self._models is None:
            self._connect()
        return self._models.execute_kw(self.db_name, self._uid, self.api_key,
                                       model, method, list(args), kw or {})


def _find_partner(client: TenantOdoo, email: str):
    rows = client.exec("res.partner", "search_read",
                       [["email", "=ilike", email]], fields=["id", "name"], limit=1)
    return rows[0]["id"] if rows else None


def _find_open_lead(client: TenantOdoo, partner_id: int):
    """Een open kans (opportunity) voor dit contact, of None. `active` sluit gearchiveerde
    en verloren leads uit; de nieuwste wint."""
    try:
        rows = client.exec(
            "crm.lead", "search_read",
            [["partner_id", "=", partner_id], ["type", "=", "opportunity"], ["active", "=", True]],
            fields=["id"], limit=1, order="id desc",
        )
        return rows[0]["id"] if rows else None
    except Exception:  # noqa: BLE001 — geen CRM-module is geen fout, gewoon geen deal
        return None


def _note(subject: str, snippet: str, direction: str) -> str:
    pijl = "📥 Inkomende" if direction == "in" else "📤 Uitgaande"
    kop = f"{pijl} mail via Otto"
    onderwerp = (subject or "(geen onderwerp)").strip()
    body = (snippet or "").strip()
    return f"<p><b>{kop}</b><br/>{_escape(onderwerp)}</p>" + (f"<p>{_escape(body)}</p>" if body else "")


def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log_mail(conn, from_email: str, subject: str, body: str, direction: str = "in") -> dict:
    """Log één mail bij het juiste Odoo-contact (en op de open deal, als die er is).

    Geeft een dict terug met wat er gebeurd is. Best-effort — een hapering geeft
    {"logged": False} terug in plaats van te crashen."""
    email = (from_email or "").strip()
    if not (getattr(conn, "is_ready", False) and email):
        return {"logged": False, "reason": "not_ready"}
    try:
        client = TenantOdoo(conn)
        partner_id = _find_partner(client, email)
        if not partner_id:
            return {"logged": False, "reason": "no_partner"}
        snippet = (body or "")[:400]
        note = _note(subject, snippet, direction)
        client.exec("res.partner", "message_post", [partner_id],
                    body=note, subject=(subject or "")[:200])
        lead_id = _find_open_lead(client, partner_id)
        if lead_id:
            client.exec("crm.lead", "message_post", [lead_id],
                        body=note, subject=(subject or "")[:200])
        current_app.logger.info("[odoo-crm] mail gelogd bij contact %s (lead %s)", partner_id, lead_id)
        return {"logged": True, "partner_id": partner_id, "lead_id": lead_id}
    except Exception as exc:  # noqa: BLE001 — de eigen Odoo van de klant mag Otto niet breken
        current_app.logger.warning("[odoo-crm] loggen mislukt: %s", exc)
        return {"logged": False, "reason": "error"}

"""Het contract voor één CRM-connector, plus de gedeelde HTTP-hulp.

Elke connector werkt tegen het CRM van de klant zelf (één `CrmConnection`-rij) en
volgt dezelfde grondregels als de oorspronkelijke Odoo-koppeling:

- alleen loggen bij een contact dat al in het CRM bestaat — wie mailt en er niet in
  staat, is geen klant of lead; daar een contact voor aanmaken zou het CRM vervuilen
  (de webhook-connector is de bewuste uitzondering: die stuurt alles door en laat
  het filteren aan de ontvanger);
- alles best-effort — `log_mail` geeft {"logged": False, "reason": ...} terug in
  plaats van te crashen, want het CRM van de klant mag de mailverwerking nooit breken.

`test_connection` mag WEL de echte fout teruggeven: die draait alleen op de knop
"Test verbinding" in Instellingen, waar de beheerder juist wil zien wat er misloopt.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# Eén vaste, korte timeout voor alle CRM-verkeer: het loopt midden in de
# mailpijplijn, dus liever een gemiste log dan een hangende verwerking.
HTTP_TIMEOUT = 10


class CRMError(RuntimeError):
    """Een leesbare fout uit het CRM — voor de testknop, nooit voor de pijplijn."""


def http_json(method: str, url: str, payload: dict | None = None,
              headers: dict | None = None) -> dict:
    """Eén JSON-call met timeout. Geeft de gedecodeerde body terug, of raiset
    CRMError met een korte, toonbare reden (statuscode + eerste stuk body)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 — URL komt uit de eigen koppeling van de klant
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise CRMError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 — DNS, timeout, TLS, …
        raise CRMError(str(exc)) from exc
    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        return {}


def snippet_of(body: str | None, limit: int = 400) -> str:
    return (body or "").strip()[:limit]


class CRMConnector:
    """Basisklasse: één connector per gekoppeld CRM."""

    provider = ""

    def __init__(self, conn):
        self.conn = conn

    def log_mail(self, from_email: str, subject: str, body: str,
                 direction: str = "in") -> dict:
        """Log één mail in het CRM. Best-effort; geeft altijd een dict terug."""
        raise NotImplementedError

    def test_connection(self) -> dict:
        """Live verbindingstest: {"ok": bool, "detail": str}. Mag de echte fout tonen."""
        raise NotImplementedError

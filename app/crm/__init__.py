"""Standaard-CRM-koppelingen van de klant zelf.

Elke organisatie koppelt hier haar eigen CRM (Instellingen → Integraties): Odoo,
HubSpot, Pipedrive, of — voor al de rest — een generieke webhook. Otto logt er
inkomende klantmail bij het juiste contact; de connectors delen één contract
(`app/crm/base.py`) en één opslagmodel (`CrmConnection`).

`PROVIDERS` is de registry: hij bepaalt wélke koppelingen bestaan, hoe hun
instellingenkaart eruitziet (velden, labels) en welke connector-klasse erachter
zit. Een nieuw CRM toevoegen = één connector-module + één registry-entry.
"""
from __future__ import annotations

from flask_babel import lazy_gettext as _l

from app.crm.hubspot import HubSpotConnector
from app.crm.odoo import OdooConnector
from app.crm.pipedrive import PipedriveConnector
from app.crm.webhook import WebhookConnector

# Volgorde = volgorde van de kaarten op de Integraties-pagina.
PROVIDERS = {
    "odoo": {
        "name": "Odoo",
        "tag": "Od",
        "color": "#714B67",
        "description": _l("Koppel je eigen Odoo, dan logt Otto inkomende klantmail bij het "
                          "juiste contact — en op de open deal als die er is. Mail van iemand "
                          "die niet in je Odoo staat, blijft ongemoeid."),
        "connector": OdooConnector,
        "fields": [
            {"name": "base_url", "label": _l("Odoo-URL"), "type": "url",
             "placeholder": "https://mijnbedrijf.odoo.com"},
            {"name": "db_name", "label": _l("Database"), "type": "text", "placeholder": ""},
            {"name": "username", "label": _l("Gebruiker (e-mail)"), "type": "text",
             "placeholder": ""},
            {"name": "api_key", "label": _l("API-sleutel"), "type": "password",
             "placeholder": ""},
        ],
    },
    "hubspot": {
        "name": "HubSpot",
        "tag": "Hu",
        "color": "#FF7A59",
        "description": _l("Koppel HubSpot met een private-app-token (scopes: contacten lezen "
                          "en schrijven), dan logt Otto inkomende klantmail als notitie bij "
                          "het bestaande contact. Onbekende afzenders blijven ongemoeid."),
        "connector": HubSpotConnector,
        "fields": [
            {"name": "api_key", "label": _l("Private-app-token"), "type": "password",
             "placeholder": "pat-eu1-…"},
        ],
    },
    "pipedrive": {
        "name": "Pipedrive",
        "tag": "Pi",
        "color": "#1A1A1A",
        "description": _l("Koppel Pipedrive met je persoonlijke API-token, dan logt Otto "
                          "inkomende klantmail als notitie bij de bestaande persoon — en op "
                          "de open deal als die er is."),
        "connector": PipedriveConnector,
        "fields": [
            {"name": "api_key", "label": _l("API-token"), "type": "password",
             "placeholder": ""},
        ],
    },
    "webhook": {
        "name": _l("Webhook (elk ander CRM)"),
        "tag": "↗",
        "color": "#2D6BDB",
        "description": _l("Nog een ander CRM? Otto stuurt elke inkomende klantmail als "
                          "JSON-bericht naar een URL van jou — rechtstreeks, of via "
                          "Zapier/Make/n8n door naar bv. Salesforce of Teamleader. Met een "
                          "geheim tekent Otto elk bericht (HMAC-SHA256, header "
                          "X-Otto-Signature)."),
        "connector": WebhookConnector,
        "fields": [
            {"name": "base_url", "label": _l("Webhook-URL"), "type": "url",
             "placeholder": "https://hooks.zapier.com/…"},
            {"name": "api_key", "label": _l("Geheim voor ondertekening (optioneel)"),
             "type": "password", "placeholder": ""},
        ],
    },
}


def get_connector(conn):
    """De connector-instantie voor één CrmConnection-rij, of None bij een onbekende
    provider (bv. een rij van een nieuwere versie na een rollback)."""
    meta = PROVIDERS.get(conn.provider or "")
    return meta["connector"](conn) if meta else None

"""Inkomende klantmail loggen in de gekoppelde CRM's van de organisatie.

Het loggen zelf staat in de connectors onder `app/crm/`; hier staat alleen de brug
vanuit de mailverwerking: elke ingeschakelde, volledig ingevulde koppeling van de
organisatie krijgt de mail. Best-effort en gescoped op de organisatie — een
haperende koppeling mag de rest van de pijplijn (én de andere koppelingen) niet raken.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import CrmConnection


def _connections_for(org_id):
    if not org_id:
        return []
    return (
        db.session.query(CrmConnection)
        .filter_by(organization_id=org_id, enabled=True)
        .order_by(CrmConnection.id)
        .all()
    )


def log_customer_mail(message) -> bool:
    """Log een binnengekomen bericht in elk gekoppeld CRM. Geeft True als er in
    minstens één CRM echt iets gelogd is."""
    from app.crm import get_connector

    logged_any = False
    for conn in _connections_for(getattr(message, "organization_id", None)):
        if not conn.is_ready or not conn.log_incoming:
            continue
        connector = get_connector(conn)
        if connector is None:
            continue
        try:
            result = connector.log_mail(
                from_email=message.sender_email or "",
                subject=message.subject or "",
                body=message.body or "",
                direction="in",
            )
        except Exception:  # noqa: BLE001
            current_app.logger.info("[crm:%s] loggen overgeslagen voor bericht %s",
                                    conn.provider, getattr(message, "id", "?"))
            continue
        logged_any = logged_any or bool(result.get("logged"))
    return logged_any

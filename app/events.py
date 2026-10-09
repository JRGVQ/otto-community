"""Org-event recording — the business timeline of a customer environment.

Why a controlled vocabulary: this feed is the one place where the platform owner sees
activity across every tenant, and Otto deliberately has no cross-tenant data console
(see the note in app/__init__.py). Restricting events to a fixed catalogue, with a short
`detail` that carries only account facts, keeps that promise structural instead of a
convention someone forgets — mailbox content can never leak in through a stray f-string.

Recording is best-effort: a failed event must never sink the action it describes.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import OrgEvent

# The catalogue. A key not listed here is refused, so the feed stays filterable and safe.
EVENT_LABELS: dict[str, str] = {
    # Levenscyclus van de omgeving
    "org.created": "Omgeving aangemaakt",
    "org.verified": "E-mailadres geverifieerd",
    "org.deleted": "Omgeving verwijderd",
    "org.restored": "Omgeving hersteld",
    # Toegang (actief / geschorst door de beheerder)
    "license.status_changed": "Toegang gewijzigd",
    # Team
    "member.invited": "Teamlid uitgenodigd",
    "member.joined": "Teamlid toegevoegd",
    "member.removed": "Teamlid verwijderd",
    # Koppelingen
    "mailbox.connected": "Mailbox gekoppeld",
    "mailbox.disconnected": "Mailbox ontkoppeld",
    # Beveiliging
    "domain.verified": "Domein geverifieerd",
    "domain.revoked": "Domeinverificatie vervallen",
    "sso.configured": "SSO ingesteld",
    "owner.impersonated": "Bekeken door de beheerder",
}

# Which events belong to which lens on the owner screen.
EVENT_GROUPS: dict[str, tuple[str, ...]] = {
    "lifecycle": ("org.created", "org.verified", "org.deleted", "org.restored"),
    "license": ("license.status_changed",),
    "team": ("member.invited", "member.joined", "member.removed"),
    "integrations": ("mailbox.connected", "mailbox.disconnected"),
    "security": ("domain.verified", "domain.revoked", "sso.configured", "owner.impersonated"),
}

GROUP_LABELS = {
    "lifecycle": "Levenscyclus", "license": "Toegang", "team": "Team",
    "integrations": "Koppelingen", "security": "Beveiliging",
}


def label_for(event: str) -> str:
    return EVENT_LABELS.get(event, event)


def record_event(org, event: str, *, detail: str | None = None, actor: str | None = None) -> OrgEvent | None:
    """Append one business event to an org's timeline. Never raises.

    Does NOT commit: the event joins the caller's transaction, so it lands exactly when
    the change it describes does — an event for a rolled-back action is never persisted.
    """
    if org is None or event not in EVENT_LABELS:
        if event not in EVENT_LABELS:
            current_app.logger.warning("record_event: onbekende gebeurtenis %r genegeerd", event)
        return None
    try:
        row = OrgEvent(
            organization_id=org.id, event=event,
            detail=(detail or None) and str(detail)[:255],
            actor=(actor or None) and str(actor)[:64],
        )
        db.session.add(row)
        return row
    except Exception:  # noqa: BLE001 — logging must never break the action it describes
        current_app.logger.warning("record_event faalde voor %s", event, exc_info=True)
        return None


def events_for(org, limit: int = 100) -> list[OrgEvent]:
    """One organisation's timeline, newest first."""
    if org is None:
        return []
    return (
        db.session.query(OrgEvent)
        .filter_by(organization_id=org.id)
        .order_by(OrgEvent.occurred_at.desc(), OrgEvent.id.desc())
        .limit(limit)
        .all()
    )


def recent_events(*, group: str | None = None, org_id: int | None = None, limit: int = 300) -> list[OrgEvent]:
    """The cross-tenant feed for the platform backoffice, newest first."""
    q = db.session.query(OrgEvent)
    if org_id:
        q = q.filter(OrgEvent.organization_id == org_id)
    if group and group in EVENT_GROUPS:
        q = q.filter(OrgEvent.event.in_(EVENT_GROUPS[group]))
    return q.order_by(OrgEvent.occurred_at.desc(), OrgEvent.id.desc()).limit(limit).all()

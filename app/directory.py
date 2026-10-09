"""CRM-logica: passief contacten/bedrijven opbouwen + de standaardcategorie bepalen.

`collect_from_message` draait in de pijplijn en houdt het
adresboek bij; `default_category_for` levert een deterministische categorie (contact
vóór bedrijf) die de AI-classificatie overschrijft — voorspelbaar en zonder AI-budget.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import Company, Contact


def _domain_of(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email.rsplit("@", 1)[-1] if "@" in email else None


def upsert_contact(org, email: str, name: str | None = None) -> Contact | None:
    """Zoek of maak een contact (+ zijn bedrijf) voor dit e-mailadres. None bij een
    ongeldig adres."""
    email = (email or "").strip().lower()
    domain = _domain_of(email)
    if not domain or org is None:
        return None
    contact = (
        db.session.query(Contact)
        .filter_by(organization_id=org.id, email=email)
        .first()
    )
    if contact is None:
        company = (
            db.session.query(Company)
            .filter_by(organization_id=org.id, domain=domain)
            .first()
        )
        if company is None:
            company = Company(organization_id=org.id, domain=domain)
            db.session.add(company)
            db.session.flush()
        contact = Contact(organization_id=org.id, email=email, company_id=company.id,
                          message_count=0)
        db.session.add(contact)
        db.session.flush()
    if name and not contact.name:
        contact.name = name[:255]
    return contact


def collect_from_message(message) -> None:
    """Werk het adresboek bij op basis van de afzender van dit bericht. Best-effort —
    een CRM-hapering mag de mailverwerking nooit breken."""
    org = getattr(message, "organization", None)
    if org is None:
        from app.models import Organization

        org = db.session.get(Organization, message.organization_id)
    if org is None:
        return
    try:
        contact = upsert_contact(org, message.sender_email, message.sender)
        if contact is not None:
            contact.message_count = (contact.message_count or 0) + 1
            contact.last_seen = message.received_at or contact.last_seen
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.info("[crm] collect overgeslagen voor bericht %s: %s", message.id, exc)


def default_category_for(message):
    """De standaardcategorie voor dit bericht uit de CRM, of None.

    Contact wint van bedrijf.
    Deze categorie is deterministisch en overschrijft de AI-classificatie."""
    org = getattr(message, "organization", None)
    org_id = message.organization_id
    if org is None:
        from app.models import Organization

        org = db.session.get(Organization, org_id)
    if org is None:
        return None
    email = (message.sender_email or "").strip().lower()
    if not email:
        return None
    contact = db.session.query(Contact).filter_by(organization_id=org_id, email=email).first()
    if contact is not None and contact.default_category_id:
        return contact.default_category
    domain = _domain_of(email)
    if domain:
        company = db.session.query(Company).filter_by(organization_id=org_id, domain=domain).first()
        if company is not None and company.default_category_id:
            return company.default_category
    return None

"""Schermcontext voor het CRM (bedrijven/contacten + projecten)."""
from __future__ import annotations


from app.extensions import db
from app.models import Company, Contact, Project, ProjectMessage

from app.web.context._base import _categories, _org, _personal_messages  # noqa: F401


def crm_context() -> dict:
    org = _org()
    companies = (
        db.session.query(Company)
        .filter_by(organization_id=org.id)
        .order_by(Company.name.is_(None), Company.name, Company.domain)
        .all()
    )
    contacts_by_company: dict[int, list] = {}
    for c in (db.session.query(Contact)
              .filter_by(organization_id=org.id)
              .order_by(Contact.message_count.desc(), Contact.email).all()):
        contacts_by_company.setdefault(c.company_id, []).append(c)
    return {
        "companies": companies,
        "contacts_by_company": contacts_by_company,
        "cats": _categories(),
        "company_count": len(companies),
        "contact_count": sum(len(v) for v in contacts_by_company.values()),
    }


def projects_context() -> dict:
    org = _org()
    projects = (
        db.session.query(Project)
        .filter_by(organization_id=org.id)
        .order_by(Project.status, Project.name)
        .all()
    )
    counts = {}
    for p in projects:
        counts[p.id] = db.session.query(ProjectMessage).filter_by(project_id=p.id).count()
    # Recente mail om naar een project te slepen (persoonlijke inbox, nieuwste eerst).
    # Via _personal_messages(): een kale org-brede Message-query toonde hier onderwerp en
    # afzender van de mail van álle collega's — inclusief gedeelde postvakken waar deze
    # gebruiker geen lid van is. Seed-mail hoort niet in een CRM-project.
    recent = [m for m in _personal_messages() if not m.is_seed][:25]
    return {"projects": projects, "project_msg_counts": counts, "recent_messages": recent}


def project_detail_context(project: Project) -> dict:
    links = (
        db.session.query(ProjectMessage)
        .filter_by(project_id=project.id)
        .order_by(ProjectMessage.id.desc())
        .all()
    )
    return {
        "project": project,
        "linked_messages": [pl.message for pl in links if pl.message],
        "project_contacts": [m.contact for m in project.members if m.contact],
    }

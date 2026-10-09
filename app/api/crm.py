"""CRM-endpoints: standaardcategorie per bedrijf/contact, projecten, mail→project.

Alles org-scoped. De frontend gebruikt deze via fetch
(otto.js), inclusief de drag-and-drop van mail naar een project.
"""
from __future__ import annotations

from flask import abort, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import fail, get_message, ok
from app.extensions import db
from app.models import Category, Company, Contact, Project, ProjectMessage


def _payload():
    return request.get_json(silent=True) or request.form


def _category_or_none(cat_id):
    """Valideer een categorie-id binnen de eigen organisatie; leeg = wissen (None)."""
    if not cat_id:
        return None
    cat = db.session.get(Category, int(cat_id))
    if not cat or cat.organization_id != current_user.organization_id:
        abort(404)
    return cat


@api_bp.route("/crm/company/<int:company_id>/category", methods=["POST"])
@login_required
def crm_company_category(company_id: int):
    company = db.session.get(Company, company_id)
    if not company or company.organization_id != current_user.organization_id:
        abort(404)
    cat = _category_or_none(_payload().get("category_id"))
    company.default_category_id = cat.id if cat else None
    db.session.commit()
    return ok(toast=_("Standaardcategorie opgeslagen."))


@api_bp.route("/crm/contact/<int:contact_id>/category", methods=["POST"])
@login_required
def crm_contact_category(contact_id: int):
    contact = db.session.get(Contact, contact_id)
    if not contact or contact.organization_id != current_user.organization_id:
        abort(404)
    cat = _category_or_none(_payload().get("category_id"))
    contact.default_category_id = cat.id if cat else None
    db.session.commit()
    return ok(toast=_("Standaardcategorie opgeslagen."))


@api_bp.route("/crm/projects", methods=["POST"])
@login_required
def crm_project_create():
    name = (_payload().get("name") or "").strip()[:255]
    if not name:
        return fail(_("Geef het project een naam."), 400)
    project = Project(organization_id=current_user.organization_id, name=name,
                      color=(_payload().get("color") or "#6C4CE6")[:9])
    db.session.add(project)
    db.session.commit()
    return ok(id=project.id, toast=_("Project aangemaakt."))


@api_bp.route("/crm/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def crm_project_delete(project_id: int):
    project = db.session.get(Project, project_id)
    if not project or project.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(project)
    db.session.commit()
    return ok(toast=_("Project verwijderd."))


@api_bp.route("/crm/projects/<int:project_id>/messages", methods=["POST"])
@login_required
def crm_project_add_message(project_id: int):
    """Koppel een bericht aan een project (de 'sleep naar project'-actie)."""
    project = db.session.get(Project, project_id)
    if not project or project.organization_id != current_user.organization_id:
        abort(404)
    msg = get_message(int(_payload().get("message_id") or 0))
    existing = (db.session.query(ProjectMessage)
                .filter_by(project_id=project.id, message_id=msg.id).first())
    if existing is None:
        db.session.add(ProjectMessage(organization_id=current_user.organization_id,
                                      project_id=project.id, message_id=msg.id))
        db.session.commit()
    return ok(toast=_("Toegevoegd aan “%(name)s”.", name=project.name))


@api_bp.route("/crm/projects/<int:project_id>/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def crm_project_remove_message(project_id: int, message_id: int):
    link = (db.session.query(ProjectMessage)
            .join(Project, ProjectMessage.project_id == Project.id)
            .filter(ProjectMessage.project_id == project_id,
                    ProjectMessage.message_id == message_id,
                    Project.organization_id == current_user.organization_id)
            .first())
    if link is None:
        abort(404)
    db.session.delete(link)
    db.session.commit()
    return ok(toast=_("Losgekoppeld van het project."))

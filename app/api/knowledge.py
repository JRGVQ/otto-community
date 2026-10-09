"""Knowledge-source endpoints — connect/remove a SharePoint library for RAG grounding.

Admin-only (manageProviders). The library is searched live at draft time; we store only
the reference (name + URL), never document content.
"""
from __future__ import annotations

from flask import request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import ok
from app.extensions import db
from app.models import AuditLog, KnowledgeSource
from app.security.rbac import require_perm


@api_bp.route("/knowledge", methods=["POST"])
@login_required
@require_perm("manageProviders")
def add_knowledge():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    site_url = (data.get("site_url") or "").strip()
    if not site_url:
        return ok(error="Geef de SharePoint-URL op."), 400
    src = KnowledgeSource(
        organization_id=current_user.organization_id, type="sharepoint",
        name=name or site_url, site_url=site_url, library_path=site_url, status="connected",
    )
    db.session.add(src)
    db.session.add(AuditLog(
        organization_id=current_user.organization_id, actor=current_user.name,
        action=f"koppelde kennisbron {name or site_url}", kind="provider", occurred_display="zojuist",
    ))
    db.session.commit()
    return ok(toast="Kennisbron gekoppeld.", id=src.id)


@api_bp.route("/knowledge/<int:source_id>", methods=["DELETE"])
@login_required
@require_perm("manageProviders")
def remove_knowledge(source_id: int):
    src = db.session.get(KnowledgeSource, source_id)
    if not src or src.organization_id != current_user.organization_id:
        return ok(error="onbekend"), 404
    db.session.delete(src)
    db.session.commit()
    return ok(toast="Kennisbron verwijderd.")

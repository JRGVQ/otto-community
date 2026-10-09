"""Category CRUD (name, colour, AI instruction, subcategories, draft toggle)."""
from __future__ import annotations

from flask import flash, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import get_category, ok
from app.extensions import db
from app.models import Category, Message, Subcategory
from app.security.rbac import require_perm


def _payload():
    return request.get_json(silent=True) or {}


def _apply_subs(cat: Category, subs: list[dict]) -> None:
    for s in list(cat.subcategories):
        db.session.delete(s)
    for i, s in enumerate(subs or []):
        name = (s.get("name") or "").strip()
        if name:
            db.session.add(Subcategory(
                category_id=cat.id, name=name, ai=s.get("ai", ""),
                draft_enabled=bool(s.get("draft_enabled")), position=i,
            ))


@api_bp.route("/categories", methods=["POST"])
@login_required
@require_perm("manageOwnCats")
def create_category():
    data = _payload()
    name = (data.get("name") or "").strip()
    if not name:
        return ok(error=_("Geef de categorie een naam")), 400
    count = db.session.query(Category).filter_by(organization_id=current_user.organization_id).count()
    keep_in_inbox = data.get("keep_in_inbox", True)
    cat = Category(
        organization_id=current_user.organization_id, key="tmp", name=name,
        color=data.get("color", "#6C4CE6"), ai=data.get("ai") or "Geen instructie ingesteld.",
        draft_enabled=bool(data.get("draft_enabled")) and not data.get("is_noise"),
        is_default=False, is_org_template=True,
        position=count,
        keep_in_inbox=bool(keep_in_inbox),
        target_folder=None if keep_in_inbox else ((data.get("target_folder") or "").strip() or None),
        is_noise=bool(data.get("is_noise")),
    )
    db.session.add(cat)
    db.session.flush()
    cat.key = f"cat_{cat.id}"
    _apply_subs(cat, data.get("subs", []))
    db.session.commit()
    flash(_("Categorie opgeslagen"), "toast")
    return ok(reload=True)


@api_bp.route("/categories/<int:category_id>", methods=["PUT"])
@login_required
@require_perm("manageOwnCats")
def update_category(category_id: int):
    cat = get_category(category_id)
    data = _payload()
    name = (data.get("name") or "").strip()
    if not name:
        return ok(error=_("Geef de categorie een naam")), 400
    from app.outlook_labels import label_for

    old_label = label_for(cat, cat.organization)  # vóór de wijziging, voor de her-tag
    cat.name = name
    cat.color = data.get("color", cat.color)
    cat.ai = data.get("ai", cat.ai)
    cat.draft_enabled = bool(data.get("draft_enabled"))
    keep_in_inbox = data.get("keep_in_inbox", cat.keep_in_inbox)
    cat.keep_in_inbox = bool(keep_in_inbox)
    cat.target_folder = None if keep_in_inbox else ((data.get("target_folder") or "").strip() or None)
    # Ruis en concepten sluiten elkaar uit: mail die geen mens vraagt, vraagt ook geen
    # antwoord. Zo kan het scherm nooit een categorie opleveren die allebei belooft.
    cat.is_noise = bool(data.get("is_noise"))
    if cat.is_noise:
        cat.draft_enabled = False
    _apply_subs(cat, data.get("subs", []))
    db.session.commit()

    # Hernoemd? Dan verhuist bestaande Outlook-mail mee naar de nieuwe labelnaam —
    # Graph kan een categorie niet hernoemen, dus zonder her-tag bleef het oude label
    # naast het nieuwe staan (zie app/tasks/retag.py). Best-effort, per mailbox.
    new_label = label_for(cat, cat.organization)
    if new_label != old_label:
        from app.tasks.retag import retag_org_labels

        retag_org_labels(cat.organization, {old_label: (new_label, cat.color)})

    flash(_("Categorie opgeslagen"), "toast")
    return ok(reload=True)


@api_bp.route("/categories/<int:category_id>", methods=["DELETE"])
@login_required
@require_perm("manageOwnCats")
def delete_category(category_id: int):
    cat = get_category(category_id)
    # Reassign affected mail to the first remaining category to avoid orphans.
    fallback = (
        db.session.query(Category)
        .filter(Category.organization_id == cat.organization_id, Category.id != cat.id)
        .order_by(Category.position).first()
    )
    for m in db.session.query(Message).filter_by(category_id=cat.id):
        m.category_id = fallback.id if fallback else None
        # De oude stempel (filter/ai/user) sloeg op de verwijderde categorie; voor de
        # fallback weten we het niet — en een valse "filter"-stempel zou het
        # ruis-rapport "AI-aanroepen bespaard" laten tellen die nooit bespaard zijn.
        m.classified_by = None
    db.session.delete(cat)
    db.session.commit()
    flash(_("Categorie verwijderd"), "toast")
    return ok(reload=True)

"""Task endpoints: toggle / edit / delete, steps (subtasks), and To Do lists.

Mutations go through app/tasks/task_ops.py, which also pushes the change to Microsoft
To Do (two-way) or Planner where the task is backed by one.
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import get_task, ok
from app.extensions import db
from app.models import TaskAttachment, TaskStep
from app.security.rbac import require_perm
from app.tasks import task_ops


def _get_step(task_id: int, step_id: int) -> TaskStep:
    task = get_task(task_id)  # org-scoped ownership check
    step = db.session.get(TaskStep, step_id)
    if not step or step.task_id != task.id:
        abort(404)
    return step


def _get_attachment(task_id: int, att_id: int) -> TaskAttachment:
    task = get_task(task_id)
    att = db.session.get(TaskAttachment, att_id)
    if not att or att.task_id != task.id:
        abort(404)
    return att


@api_bp.route("/tasks/<int:task_id>/toggle", methods=["POST"])
@login_required
@require_perm("editDrafts")
def toggle_task(task_id: int):
    task = task_ops.toggle_done(get_task(task_id))
    return ok(done=task.done)


@api_bp.route("/tasks/<int:task_id>", methods=["PATCH", "POST"])
@login_required
@require_perm("editDrafts")
def update_task(task_id: int):
    data = request.get_json(silent=True) or {}
    task_ops.update_fields(get_task(task_id), data)
    return ok(reload=True, toast="Taak bijgewerkt.")


@api_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
@login_required
@require_perm("editDrafts")
def delete_task(task_id: int):
    task_ops.delete_task(get_task(task_id))
    return ok(reload=True, toast="Taak verwijderd.")


# ── Steps (subtasks) ──────────────────────────────────────────────────────────
@api_bp.route("/tasks/<int:task_id>/steps", methods=["POST"])
@login_required
@require_perm("editDrafts")
def add_step(task_id: int):
    title = ((request.get_json(silent=True) or {}).get("title") or "").strip()
    if not title:
        return ok(error="Geef de stap een titel."), 400
    task_ops.add_step(get_task(task_id), title)
    return ok(reload=True)


@api_bp.route("/tasks/<int:task_id>/steps/<int:step_id>/toggle", methods=["POST"])
@login_required
@require_perm("editDrafts")
def toggle_step(task_id: int, step_id: int):
    step = task_ops.toggle_step(_get_step(task_id, step_id))
    return ok(done=step.done)


@api_bp.route("/tasks/<int:task_id>/steps/<int:step_id>", methods=["DELETE"])
@login_required
@require_perm("editDrafts")
def delete_step(task_id: int, step_id: int):
    task_ops.delete_step(_get_step(task_id, step_id))
    return ok(reload=True)


# ── Attachments ───────────────────────────────────────────────────────────────
@api_bp.route("/tasks/<int:task_id>/attachments", methods=["POST"])
@login_required
@require_perm("editDrafts")
def upload_attachment(task_id: int):
    task = get_task(task_id)
    f = request.files.get("file")
    if not f or not f.filename:
        return ok(error="Geen bestand ontvangen."), 400
    att, err = task_ops.add_attachment(task, f.filename, f.mimetype, f.read())
    if err:
        return ok(error=err), 400
    return ok(reload=True, toast="Bijlage toegevoegd.")


@api_bp.route("/tasks/<int:task_id>/attachments/<int:att_id>", methods=["DELETE"])
@login_required
@require_perm("editDrafts")
def delete_attachment(task_id: int, att_id: int):
    task_ops.delete_attachment(_get_attachment(task_id, att_id))
    return ok(reload=True)


# ── To Do lists ───────────────────────────────────────────────────────────────
@api_bp.route("/todo/lists")
@login_required
def todo_lists():
    lists = [
        {"id": l.id, "name": l.name, "synced": bool(l.provider_list_id), "is_default": l.is_default}
        for l in task_ops.user_lists(current_user)
    ]
    return ok(lists=lists)


@api_bp.route("/todo/lists", methods=["POST"])
@login_required
@require_perm("editDrafts")
def create_todo_list():
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return ok(error="Geef de lijst een naam."), 400
    lst = task_ops.create_list(current_user, name)
    return ok(reload=True, toast="Lijst aangemaakt.", id=lst.id)

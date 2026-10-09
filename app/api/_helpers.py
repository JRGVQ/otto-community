"""Shared API helpers — org-scoped lookups and JSON responses."""
from __future__ import annotations

from flask import abort, jsonify
from flask_login import current_user

from app.extensions import db
from app.models import Category, Draft, Message, Task


def ok(**payload):
    return jsonify({"ok": True, **payload})


def fail(error: str, status: int = 400):
    """Een weigering die de client ook als weigering ZIET: ok=False.

    `ok(error=...), 404` stuurt {"ok": true, ...} — otto.js kijkt naar data.ok en
    behandelt zo'n antwoord als succes (de schakelaar klapt om zonder dat er iets is
    opgeslagen). Gebruik dit voor guards die de UI moet kunnen onderscheiden."""
    return jsonify({"ok": False, "error": error}), status


def get_message(message_id: int) -> Message:
    msg = db.session.get(Message, message_id)
    # Personal mail is private to its owner; shared-mailbox mail is visible to members.
    if not msg or not msg.visible_to(current_user):
        abort(404)
    return msg


def get_category(category_id: int) -> Category:
    cat = db.session.get(Category, category_id)
    if not cat or cat.organization_id != current_user.organization_id:
        abort(404)
    return cat


def get_task(task_id: int) -> Task:
    task = db.session.get(Task, task_id)
    # Tasks are personal — scope to the owning user, not just the organisation.
    if (
        not task
        or task.organization_id != current_user.organization_id
        or task.user_id != current_user.id
    ):
        abort(404)
    return task


def get_draft_for(message_id: int) -> Draft | None:
    return get_message(message_id).draft

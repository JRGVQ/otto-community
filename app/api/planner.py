"""Microsoft Planner picker data (plans / buckets / directory users) + the standalone
task-create endpoint. Task creation itself lives in app/tasks/task_ops.py (shared with
the mail flow and the To Do two-way sync)."""
from __future__ import annotations

from flask import current_app, request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import get_message, ok
from app.security.rbac import require_perm
from app.tasks.task_ops import create_task, graph_account_for, task_toast


def _planner(user):
    from app.providers.planner import PlannerProvider

    acct = graph_account_for(user)
    return PlannerProvider(acct) if acct is not None else None


# ── Picker data ──────────────────────────────────────────────────────────────
@api_bp.route("/planner/plans")
@login_required
def planner_plans():
    p = _planner(current_user)
    if p is None or not p.is_configured():
        return ok(configured=False, plans=[])
    try:
        return ok(configured=True, plans=p.list_plans())
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("planner_plans failed: %s", exc)
        return ok(configured=True, plans=[], error="Kon de plannen niet ophalen.")


@api_bp.route("/planner/plans/<plan_id>/buckets")
@login_required
def planner_buckets(plan_id: str):
    p = _planner(current_user)
    if p is None or not p.is_configured():
        return ok(buckets=[])
    try:
        return ok(buckets=p.list_buckets(plan_id))
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("planner_buckets failed: %s", exc)
        return ok(buckets=[], error="Kon de buckets niet ophalen.")


@api_bp.route("/planner/users")
@login_required
def planner_users():
    p = _planner(current_user)
    if p is None or not p.is_configured():
        return ok(users=[])
    try:
        return ok(users=p.search_users(request.args.get("q", "")))
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("planner_users failed: %s", exc)
        return ok(users=[], error="Kon de collega's niet ophalen.")


# ── Standalone create (Taken-pagina) ─────────────────────────────────────────
@api_bp.route("/tasks", methods=["POST"])
@login_required
@require_perm("editDrafts")
def create_standalone_task():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return ok(error="Geef de taak een titel."), 400
    message = get_message(int(data["message_id"])) if data.get("message_id") else None
    task, summary = create_task(user=current_user, title=title, message=message, data=data)
    return ok(reload=True, toast=task_toast(task, summary))

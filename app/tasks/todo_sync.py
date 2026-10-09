"""Pull Microsoft To Do into Otto (the Graph → Otto half of the two-way sync).

Idempotent and incremental: each list keeps a delta link, so a tick only fetches what
changed. The push half (Otto → Graph, synchronous on user action) lives in
`app/api/tasks_api.py`; because pushes write the returned etag/lastModifiedDateTime
straight back onto the local row, a later pull sees no diff and never clobbers a change
Otto itself just made. Remote-originated edits win only when their lastModifiedDateTime
is newer than what we last stored.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import Task, TaskAttachment, TaskStep, TodoList
from app.models.base import utcnow
from app.timefmt import parse_graph_dt


def _due_display(d) -> str:
    return d.strftime("%d-%m-%Y") if d else "Geen datum"


def pull_todo(account) -> int:
    """Pull every To Do list + its changed tasks for this account's user. Returns the
    number of tasks created/updated/removed. Best-effort: never raises to the caller."""
    from app.providers.todo import TodoProvider

    provider = TodoProvider(account)
    if not provider.is_configured():
        return 0
    changed = 0
    try:
        lists = provider.list_lists()
    except Exception as exc:  # noqa: BLE001
        current_app.logger.info("To Do list fetch skipped for account %s: %s", account.id, exc)
        return 0

    for raw_list in lists:
        todo_list = _upsert_list(account, raw_list)
        try:
            items, delta_link = provider.list_tasks(raw_list["id"], todo_list.delta_link)
        except Exception as exc:  # noqa: BLE001 — a stale delta link or one bad list must not stall the rest
            current_app.logger.info("To Do task fetch skipped for list %s: %s", raw_list["id"], exc)
            continue
        for raw in items:
            changed += _apply_task(account, todo_list, raw, provider)
        todo_list.delta_link = delta_link
        db.session.commit()
    return changed


def _upsert_list(account, raw: dict) -> TodoList:
    lst = (
        db.session.query(TodoList)
        .filter_by(user_id=account.user_id, provider_list_id=raw["id"])
        .first()
    )
    if lst is None:
        lst = TodoList(organization_id=account.organization_id, user_id=account.user_id,
                       provider_list_id=raw["id"], is_default=raw.get("is_default", False))
        db.session.add(lst)
    lst.name = raw["name"]
    db.session.flush()
    return lst


def _apply_task(account, todo_list: TodoList, raw: dict, provider) -> int:
    """Upsert one todoTask into Otto. Returns 1 if it changed anything, else 0."""
    task_id = raw.get("id")
    if not task_id:
        return 0
    existing = (
        db.session.query(Task)
        .filter_by(organization_id=account.organization_id, provider_kind="todo", provider_task_id=task_id)
        .first()
    )

    # Deletion tombstone from the delta feed.
    if raw.get("@removed") is not None:
        if existing is not None:
            db.session.delete(existing)
            return 1
        return 0

    remote_mod = parse_graph_dt(raw.get("lastModifiedDateTime"))
    if existing is not None and existing.provider_last_modified and remote_mod \
            and remote_mod <= existing.provider_last_modified:
        return 0  # remote not newer than what we already have → skip (and don't clobber a local push)

    task = existing or Task(
        organization_id=account.organization_id, user_id=account.user_id,
        provider_kind="todo", provider_task_id=task_id,
    )
    if existing is None:
        db.session.add(task)

    status = raw.get("status") or "notStarted"
    body = raw.get("body") or {}
    due = (raw.get("dueDateTime") or {}).get("dateTime")
    due_dt = parse_graph_dt(due) if due else None
    rem = (raw.get("reminderDateTime") or {}).get("dateTime")

    task.title = raw.get("title") or "(naamloos)"
    task.notes = body.get("content") or None
    task.status = status
    task.done = status == "completed"
    task.important = (raw.get("importance") == "high")
    task.due_date = due_dt.date() if due_dt else None
    task.due_display = _due_display(task.due_date)
    task.reminder_at = parse_graph_dt(rem) if (raw.get("isReminderOn") and rem) else None
    task.recurrence = provider.recurrence_from_graph(raw.get("recurrence"))
    task.recurrence_interval = provider.interval_from_graph(raw.get("recurrence"))
    task.todo_list_id = todo_list.id
    task.list_name = todo_list.name
    task.provider_etag = raw.get("@odata.etag")
    task.provider_last_modified = remote_mod or utcnow()
    db.session.flush()

    _pull_steps(provider, todo_list.provider_list_id, task)
    _pull_attachments(provider, todo_list.provider_list_id, task)
    return 1


def _pull_steps(provider, list_id: str, task: Task) -> None:
    """Mirror a task's checklistItems into TaskStep rows (full replace by provider id)."""
    try:
        raw_steps = provider.list_steps(list_id, task.provider_task_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.info("To Do steps skipped for task %s: %s", task.provider_task_id, exc)
        return
    by_id = {s.provider_step_id: s for s in task.steps if s.provider_step_id}
    seen = set()
    for i, raw in enumerate(raw_steps):
        sid = raw.get("id")
        if not sid:
            continue
        seen.add(sid)
        step = by_id.get(sid) or TaskStep(task_id=task.id, provider_step_id=sid)
        if step.id is None:
            db.session.add(step)
        step.title = raw.get("displayName") or ""
        step.done = bool(raw.get("isChecked"))
        step.position = i
    for sid, step in by_id.items():
        if sid not in seen:
            db.session.delete(step)


def _pull_attachments(provider, list_id: str, task: Task) -> None:
    """Mirror a task's file-attachment metadata into TaskAttachment rows (bytes stay in
    Graph, fetched on download). Full replace by provider id."""
    try:
        raw_atts = provider.list_attachments(list_id, task.provider_task_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.info("To Do attachments skipped for task %s: %s", task.provider_task_id, exc)
        return
    by_id = {a.provider_attachment_id: a for a in task.attachments if a.provider_attachment_id}
    seen = set()
    for raw in raw_atts:
        aid = raw.get("id")
        if not aid:
            continue
        seen.add(aid)
        att = by_id.get(aid) or TaskAttachment(task_id=task.id, provider_attachment_id=aid)
        if att.id is None:
            db.session.add(att)
        att.name = raw.get("name") or "bestand"
        att.content_type = raw.get("contentType")
        att.size = raw.get("size")
    for aid, att in by_id.items():
        if aid not in seen:
            db.session.delete(att)


def complete_tasks_for_message(message) -> int:
    """A reply was sent → tick off any linked task(s), in Otto AND in Microsoft
    To Do/Planner (via task_ops). Returns how many tasks completed. The message's own
    state is set by the caller (a sent reply means 'wacht op antwoord', not 'done')."""
    from app.tasks import task_ops

    tasks = (
        db.session.query(Task)
        .filter_by(organization_id=message.organization_id, linked_message_id=message.id, done=False)
        .all()
    )
    for t in tasks:
        task_ops.toggle_done(t)  # False → True; pushes status to the backing provider
    return len(tasks)

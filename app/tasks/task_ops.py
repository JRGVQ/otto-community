"""Task lifecycle, shared by every task entry point (create from mail, Taken page,
toggle, edit, steps). One task targets one backend (`provider_kind`): 'local',
'todo' (Microsoft To Do — full two-way) or 'planner' (Microsoft Planner — create +
assign). All Graph writes here are synchronous and best-effort: the local row is the
source of truth and a Graph hiccup never loses the change. After a successful To Do
write we absorb the returned etag/lastModifiedDateTime so the periodic pull
(`app/tasks/todo_sync.py`) sees no diff and never clobbers what Otto just wrote.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import current_app

from app.extensions import db
from app.models import Task, TaskAttachment, TaskStep, TodoList, User
from app.timefmt import parse_graph_dt

ATTACH_MAX_BYTES = 3 * 1024 * 1024  # To Do inline attachment cap (~3 MB)


# ── Field parsing ─────────────────────────────────────────────────────────────
def parse_due(raw: str | None):
    """('YYYY-MM-DD' | '') → (due_date, due_iso, due_display). Tolerant of junk."""
    raw = (raw or "").strip()
    if not raw:
        return None, None, "Geen datum"
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None, None, "Geen datum"
    return d, f"{raw}T00:00:00Z", d.strftime("%d-%m-%Y")


def _parse_dt(raw: str | None):
    """HTML datetime-local ('YYYY-MM-DDTHH:MM') → naive datetime, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _as_int(value, default: int = 1) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def graph_account_for(user):
    """A connected personal Outlook account whose delegated token drives Graph calls."""
    if user is None:
        return None
    for a in user.mail_accounts:
        if a.provider == "outlook" and a.status == "connected" and not a.is_shared:
            return a
    return None


# ── To Do push plumbing ───────────────────────────────────────────────────────
def _todo_target(task: Task):
    """(TodoProvider, list_id) for pushing this task to To Do, or (None, None)."""
    if task.provider_kind != "todo":
        return None, None
    tl = task.todo_list
    if not tl or not tl.provider_list_id:
        return None, None
    from app.providers.todo import TodoProvider

    if not TodoProvider.is_configured():
        return None, None
    acct = graph_account_for(db.session.get(User, task.user_id))
    return (TodoProvider(acct), tl.provider_list_id) if acct is not None else (None, None)


def _absorb_meta(task: Task, raw: dict | None) -> None:
    """Store To Do etag/lastModified after a write so the next pull treats it as ours."""
    if not raw:
        return
    if raw.get("@odata.etag"):
        task.provider_etag = raw["@odata.etag"]
    lm = parse_graph_dt(raw.get("lastModifiedDateTime"))
    if lm:
        task.provider_last_modified = lm


def _todo_fields(task: Task) -> dict:
    return {
        "title": task.title, "notes": task.notes, "important": task.important,
        "status": task.status, "due_date": task.due_date,
        "reminder_at": task.reminder_at, "recurrence": task.recurrence,
        "recurrence_interval": task.recurrence_interval,
    }


def _push_todo(task: Task, fields: dict) -> None:
    provider, list_id = _todo_target(task)
    if provider is None or not task.provider_task_id:
        return
    try:
        _absorb_meta(task, provider.update_task(
            list_id, task.provider_task_id, fields, etag=task.provider_etag))
    except Exception as exc:  # noqa: BLE001 — never fail a local edit on a Graph hiccup
        current_app.logger.warning("To Do push failed for task %s: %s", task.id, exc)


# ── Create ────────────────────────────────────────────────────────────────────
def create_task(*, user, title: str, message=None, data: dict | None = None):
    """Create a task and mirror it to its backend. Returns (task, summary dict)."""
    data = data or {}
    due_date, due_iso, due_display = parse_due(data.get("due"))
    notes = (data.get("notes") or "").strip() or None
    important = bool(data.get("important"))
    recurrence = (data.get("recurrence") or "").strip() or None
    recurrence_interval = _as_int(data.get("recurrence_interval"), 1)
    reminder_at = _parse_dt(data.get("reminder"))
    steps_in = [s for s in (data.get("steps") or []) if (s.get("title") or "").strip()]

    plan_id = (data.get("plan_id") or "").strip() or None
    assignee_id = (data.get("assignee_id") or "").strip() or None
    assignee_name = (data.get("assignee_name") or "").strip() or None

    todo_list = None
    if not plan_id and data.get("todo_list_id"):
        todo_list = db.session.get(TodoList, int(data["todo_list_id"]))
        if todo_list is None or todo_list.user_id != user.id:
            todo_list = None
    kind = "planner" if plan_id else ("todo" if todo_list is not None else "local")

    if message is not None:
        context = f"Van {message.sender}"
    elif assignee_name:
        context = f"Toegewezen aan {assignee_name}"
    else:
        context = None

    task = Task(
        organization_id=user.organization_id, user_id=user.id, title=title.strip()[:255],
        context=context, notes=notes, due_display=due_display, due_date=due_date,
        reminder_at=reminder_at, recurrence=recurrence, recurrence_interval=recurrence_interval,
        important=important,
        status="notStarted", done=False, urgent=False,
        list_name=(todo_list.name if todo_list else "Werk"),
        provider_kind=kind, todo_list_id=(todo_list.id if todo_list else None),
        linked_subject=(message.subject if message is not None else None),
        linked_message_id=(message.id if message is not None else None),
        plan_id=plan_id, plan_name=(data.get("plan_name") or "").strip() or None,
        bucket_id=(data.get("bucket_id") or "").strip() or None,
        bucket_name=(data.get("bucket_name") or "").strip() or None,
        assignee_id=assignee_id, assignee_name=assignee_name,
        assignee_email=(data.get("assignee_email") or "").strip() or None,
    )
    db.session.add(task)
    db.session.flush()
    for i, s in enumerate(steps_in):
        db.session.add(TaskStep(task_id=task.id, title=s["title"].strip()[:255],
                                done=bool(s.get("done")), position=i))
    db.session.flush()

    pushed = False
    if kind == "planner":
        pushed = _push_new_planner(task, user, due_iso)
    elif kind == "todo":
        pushed = _push_new_todo(task)
    db.session.commit()
    return task, {"kind": kind, "pushed": pushed}


def _push_new_planner(task: Task, user, due_iso) -> bool:
    from app.providers.planner import PlannerProvider

    acct = graph_account_for(user)
    if acct is None or not PlannerProvider.is_configured():
        return False
    try:
        pid = PlannerProvider(acct).create_task(
            task.plan_id, task.title, bucket_id=task.bucket_id,
            assignee_ids=[task.assignee_id] if task.assignee_id else None, due_iso=due_iso,
        )
        task.provider_task_id = pid or None
        return bool(pid)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Planner push failed for task %s: %s", task.id, exc)
        return False


def _push_new_todo(task: Task) -> bool:
    provider, list_id = _todo_target(task)
    if provider is None:
        return False
    try:
        raw = provider.create_task(list_id, _todo_fields(task))
        task.provider_task_id = raw.get("id") or None
        _absorb_meta(task, raw)
        for st in task.steps:
            created = provider.create_step(list_id, task.provider_task_id, st.title, st.done)
            st.provider_step_id = created.get("id")
        return bool(task.provider_task_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("To Do push failed for new task %s: %s", task.id, exc)
        return False


# ── Mutations (toggle / edit / delete / steps) ────────────────────────────────
def toggle_done(task: Task) -> Task:
    task.done = not task.done
    task.status = "completed" if task.done else "notStarted"
    _push_todo(task, {"status": task.status})
    db.session.commit()
    return task


def update_fields(task: Task, data: dict) -> Task:
    if "title" in data and (data.get("title") or "").strip():
        task.title = data["title"].strip()[:255]
    if "notes" in data:
        task.notes = (data.get("notes") or "").strip() or None
    if "important" in data:
        task.important = bool(data.get("important"))
    if "recurrence" in data:
        task.recurrence = (data.get("recurrence") or "").strip() or None
    if "recurrence_interval" in data:
        task.recurrence_interval = _as_int(data.get("recurrence_interval"), 1)
    if "due" in data:
        task.due_date, _, task.due_display = parse_due(data.get("due"))
    if "reminder" in data:
        task.reminder_at = _parse_dt(data.get("reminder"))
    _push_todo(task, _todo_fields(task))
    db.session.commit()
    return task


def delete_task(task: Task) -> None:
    provider, list_id = _todo_target(task)
    if provider is not None and task.provider_task_id:
        try:
            provider.delete_task(list_id, task.provider_task_id, etag=task.provider_etag)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do delete failed for task %s: %s", task.id, exc)
    db.session.delete(task)
    db.session.commit()


def add_step(task: Task, title: str) -> TaskStep:
    step = TaskStep(task_id=task.id, title=title.strip()[:255], done=False,
                    position=len(task.steps))
    db.session.add(step)
    db.session.flush()
    provider, list_id = _todo_target(task)
    if provider is not None and task.provider_task_id:
        try:
            step.provider_step_id = provider.create_step(list_id, task.provider_task_id, step.title).get("id")
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do step add failed: %s", exc)
    db.session.commit()
    return step


def toggle_step(step: TaskStep) -> TaskStep:
    step.done = not step.done
    provider, list_id = _todo_target(step.task)
    if provider is not None and step.provider_step_id and step.task.provider_task_id:
        try:
            provider.update_step(list_id, step.task.provider_task_id, step.provider_step_id, done=step.done)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do step toggle failed: %s", exc)
    db.session.commit()
    return step


def delete_step(step: TaskStep) -> None:
    provider, list_id = _todo_target(step.task)
    if provider is not None and step.provider_step_id and step.task.provider_task_id:
        try:
            provider.delete_step(list_id, step.task.provider_task_id, step.provider_step_id)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do step delete failed: %s", exc)
    db.session.delete(step)
    db.session.commit()


# ── Attachments ───────────────────────────────────────────────────────────────
def add_attachment(task: Task, filename: str, content_type: str, data: bytes):
    """Attach a file to a To Do-backed task. Returns (TaskAttachment|None, error|None)."""
    provider, list_id = _todo_target(task)
    if provider is None or not task.provider_task_id:
        return None, "Bijlagen kunnen alleen bij taken die in Microsoft To Do staan."
    if len(data) > ATTACH_MAX_BYTES:
        return None, "Bestand te groot (max 3 MB)."
    try:
        raw = provider.create_attachment(list_id, task.provider_task_id, filename, content_type, data)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("To Do attachment upload failed for task %s: %s", task.id, exc)
        return None, "Uploaden naar Microsoft To Do is mislukt."
    att = TaskAttachment(
        task_id=task.id, provider_attachment_id=raw.get("id"),
        name=raw.get("name") or filename, content_type=raw.get("contentType") or content_type,
        size=raw.get("size") or len(data),
    )
    db.session.add(att)
    db.session.commit()
    return att, None


def delete_attachment(att: TaskAttachment) -> None:
    provider, list_id = _todo_target(att.task)
    if provider is not None and att.provider_attachment_id and att.task.provider_task_id:
        try:
            provider.delete_attachment(list_id, att.task.provider_task_id, att.provider_attachment_id)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do attachment delete failed: %s", exc)
    db.session.delete(att)
    db.session.commit()


def attachment_bytes(att: TaskAttachment) -> bytes | None:
    """Fetch an attachment's bytes from Graph (for the download route)."""
    provider, list_id = _todo_target(att.task)
    if provider is None or not (att.provider_attachment_id and att.task.provider_task_id):
        return None
    try:
        return provider.attachment_content(list_id, att.task.provider_task_id, att.provider_attachment_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("To Do attachment fetch failed: %s", exc)
        return None


# ── Lists ─────────────────────────────────────────────────────────────────────
def user_lists(user) -> list[TodoList]:
    return (
        db.session.query(TodoList)
        .filter_by(user_id=user.id)
        .order_by(TodoList.is_default.desc(), TodoList.name)
        .all()
    )


def create_list(user, name: str) -> TodoList:
    """Create a To Do list (pushed to Microsoft To Do when connected) for this user."""
    provider_list_id = None
    acct = graph_account_for(user)
    from app.providers.todo import TodoProvider

    if acct is not None and TodoProvider.is_configured():
        try:
            provider_list_id = TodoProvider(acct).create_list(name).get("id") or None
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("To Do list create failed: %s", exc)
    lst = TodoList(organization_id=user.organization_id, user_id=user.id,
                   name=name.strip()[:128], provider_list_id=provider_list_id)
    db.session.add(lst)
    db.session.commit()
    return lst


def task_toast(task: Task, summary: dict) -> str:
    pushed = summary.get("pushed")
    if summary.get("kind") == "planner":
        who = f" en toegewezen aan {task.assignee_name}" if (pushed and task.assignee_name) else ""
        return f"Taak aangemaakt in Microsoft Planner{who}." if pushed \
            else "Taak aangemaakt in Otto — doorsturen naar Planner is niet gelukt."
    if summary.get("kind") == "todo":
        return "Taak aangemaakt in Microsoft To Do." if pushed \
            else "Taak aangemaakt in Otto — doorsturen naar To Do is niet gelukt."
    return "Taak aangemaakt."

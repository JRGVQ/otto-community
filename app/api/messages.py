"""Message endpoints: recategorise, archive, create-task, unsubscribe."""
from __future__ import annotations

from flask import flash, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import get_message, ok
from app.extensions import db
from app.models import AuditLog, Category, Feedback, Task, TodoList, UnsubscribeLog, User
from app.models.base import utcnow
from app.security.rbac import require_perm


def _payload():
    return request.get_json(silent=True) or request.form


@api_bp.route("/messages/<int:message_id>/recategorize", methods=["POST"])
@login_required
@require_perm("editDrafts")
def recategorize(message_id: int):
    msg = get_message(message_id)
    key = (request.get_json(silent=True) or request.form).get("category")
    cat = (
        db.session.query(Category)
        .filter_by(organization_id=msg.organization_id, key=key)
        .first()
    )
    if not cat:
        return ok(error=_("onbekende categorie")), 400

    old = msg.category.name if msg.category else "?"
    msg.category_id = cat.id
    msg.classified_by = "user"  # handmatig verplaatst — geen filter- of AI-besparing
    db.session.add(Feedback(user_id=msg.user_id, message_id=msg.id, kind="recategorize", from_value=old, to_value=cat.name))
    db.session.commit()
    flash(_("Verplaatst naar “%(name)s” — Otto leert hiervan", name=cat.name), "toast")
    return ok(reload=True)


@api_bp.route("/messages/<int:message_id>/archive", methods=["POST"])
@login_required
@require_perm("editDrafts")
def archive(message_id: int):
    get_message(message_id)  # ownership check
    return ok(toast=_("Gearchiveerd"))


@api_bp.route("/messages/<int:message_id>/task", methods=["POST"])
@login_required
@require_perm("editDrafts")
def create_task(message_id: int):
    """Create a task from a mail. Accepts optional list/plan/assignee + To Do fields in
    the JSON body (the task modal) and mirrors to To Do/Planner; a bare POST still creates
    an Otto task."""
    from app.tasks.task_ops import create_task as create_org_task, task_toast

    msg = get_message(message_id)
    exists = (
        db.session.query(Task)
        .filter_by(organization_id=msg.organization_id, linked_message_id=msg.id)
        .first()
    )
    if exists:
        return ok(toast=_("Er bestaat al een taak voor deze mail"))
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip() or msg.subject
    # The task belongs to whoever the mail is assigned to (else the acting user), so an
    # assigned shared-mailbox mail lands a task in that teammate's list.
    owner = (db.session.get(User, msg.assignee_user_id) if msg.assignee_user_id else None) or current_user
    if owner.id != current_user.id and not data.get("todo_list_id") and not data.get("plan_id"):
        default_list = _default_todo_list(owner)
        if default_list is not None:
            data = {**data, "todo_list_id": default_list.id}
    task, summary = create_org_task(user=owner, title=title, message=msg, data=data)
    return ok(reload=True, toast=task_toast(task, summary))


def _log_unsub(msg, method: str, target: str | None, status: str, detail: str) -> None:
    """Record an unsubscribe attempt in the dedicated log AND the org audit trail."""
    db.session.add(UnsubscribeLog(
        organization_id=msg.organization_id, user_id=current_user.id, message_id=msg.id,
        sender=msg.sender, sender_email=msg.sender_email,
        method=method, target=target, status=status, detail=detail,
    ))
    verb = {
        "done": "schreef uit", "opened": "opende de uitschrijflink",
        "failed": "kon niet uitschrijven", "no_data": "probeerde uit te schrijven",
    }.get(status, "schreef uit")
    who = msg.sender or msg.sender_email or "onbekende afzender"
    actor = (current_user.name or current_user.email or "?").split()[0]
    db.session.add(AuditLog(
        organization_id=msg.organization_id, actor=actor,
        action=f"{verb} bij {who}", kind="user",
    ))


@api_bp.route("/messages/<int:message_id>/unsubscribe", methods=["POST"])
@login_required
@require_perm("editDrafts")
def unsubscribe(message_id: int):
    """Unsubscribe from a sender's mailing list.

    Uses RFC 8058 one-click (a server-side POST) when the sender advertises it; otherwise
    hands the unsubscribe link / mailto back to the browser to open. Every attempt is
    logged (UnsubscribeLog + AuditLog) regardless of outcome."""
    from app.unsubscribe import one_click_post

    msg = get_message(message_id)
    if not msg.has_unsubscribe:
        _log_unsub(msg, "none", None, "no_data", "geen List-Unsubscribe in deze mail")
        db.session.commit()
        return ok(error=_("Geen uitschrijflink gevonden voor deze afzender.")), 400

    who = msg.sender or msg.sender_email or _("deze afzender")

    # 1) One-click (RFC 8058): unsubscribe server-side, no page to open.
    if msg.unsubscribe_oneclick and msg.unsubscribe_url:
        done, detail = one_click_post(msg.unsubscribe_url)
        _log_unsub(msg, "one_click", msg.unsubscribe_url,
                   "done" if done else "failed", detail)
        if done:
            msg.unsubscribed_at = utcnow()
            db.session.commit()
            return ok(reload=True, toast=_("Uitgeschreven bij %(who)s.", who=who))
        db.session.commit()
        # Couldn't confirm automatically — fall back to opening the page so the user finishes it.
        return ok(open_url=msg.unsubscribe_url,
                  toast=_("Automatisch uitschrijven lukte niet — we openen de uitschrijfpagina."))

    # 2) Otherwise open the link (or mailto) in the browser and record the intent.
    target = msg.unsubscribe_url or msg.unsubscribe_mailto
    method = "link" if msg.unsubscribe_url else "mailto"
    _log_unsub(msg, method, target, "opened", "geopend door gebruiker")
    msg.unsubscribed_at = utcnow()
    db.session.commit()
    label = _("uitschrijfpagina") if method == "link" else _("uitschrijfmail")
    return ok(open_url=target, toast=_("We openen de %(label)s voor %(who)s.", label=label, who=who))


def _scoped_unsub_messages(account_id) -> list | None:
    """The still-subscribed, unsubscribable mail the current user may see, in one scope.

    ``account_id`` selects a shared mailbox (membership is verified); without it we use the
    user's own personal mail. Returns ``None`` when the mailbox isn't visible → 404."""
    from app.web import context as ctx

    if account_id not in (None, "", "0", 0):
        try:
            acct = ctx._resolve_team_mailbox(int(account_id))
        except (TypeError, ValueError):
            return None
        if acct is None:
            return None
        msgs = ctx._account_messages(acct.id)
    else:
        msgs = ctx._personal_messages()
    return [m for m in msgs if m.has_unsubscribe and m.unsubscribed_at is None]


@api_bp.route("/messages/unsubscribe-all", methods=["POST"])
@login_required
@require_perm("editDrafts")
def unsubscribe_all():
    """Bulk one-click unsubscribe across the current inbox view.

    Scoped by an optional ``cat`` (category key, e.g. the Nieuwsbrief/Marketing category)
    and ``account_id`` (a shared mailbox). We group by sender so each mailing list is only
    hit once, then unsubscribe every sender that advertises RFC 8058 one-click server-side.
    Senders that only offer a link/mailto can't be handled in bulk (no page to open), so
    they're reported back as needing a manual click via their per-mail button."""
    from app.unsubscribe import one_click_post

    data = _payload()
    cat_key = (data.get("cat") or "").strip()

    msgs = _scoped_unsub_messages(data.get("account_id"))
    if msgs is None:
        return ok(error=_("Onbekend postvak.")), 404
    if cat_key and cat_key != "alle":
        msgs = [m for m in msgs if m.category and m.category.key == cat_key]

    # One entry per sender (fall back to the unsubscribe target when the address is blank),
    # so a list that mailed 20 times is unsubscribed once but all 20 mails get marked.
    by_sender: dict[str, list] = {}
    for m in msgs:
        key = (m.sender_email or m.unsubscribe_url or m.unsubscribe_mailto or f"m{m.id}").lower()
        by_sender.setdefault(key, []).append(m)

    done = manual = failed = 0
    for group in by_sender.values():
        rep = group[0]
        if rep.unsubscribe_oneclick and rep.unsubscribe_url:
            ok_done, detail = one_click_post(rep.unsubscribe_url)
            _log_unsub(rep, "one_click", rep.unsubscribe_url, "done" if ok_done else "failed", detail)
            if ok_done:
                done += 1
                for m in group:
                    m.unsubscribed_at = utcnow()
            else:
                failed += 1
        else:
            manual += 1  # link/mailto only — left for the per-mail "Uitschrijven" button
    db.session.commit()

    if done == 0 and failed == 0 and manual == 0:
        return ok(toast=_("Niets om uit te schrijven in deze weergave."))
    parts = []
    if done:
        parts.append(_("uitgeschreven bij %(n)s afzender(s)", n=done))
    if manual:
        parts.append(_("%(n)s vereisen een handmatige klik", n=manual))
    if failed:
        parts.append(_("%(n)s mislukt", n=failed))
    return ok(reload=(done > 0), toast="; ".join(parts).capitalize() + ".")


def _default_todo_list(user) -> TodoList | None:
    """The user's default To Do-synced list (so a task lands in *their* Microsoft To Do)."""
    return (
        db.session.query(TodoList)
        .filter_by(user_id=user.id, is_default=True)
        .filter(TodoList.provider_list_id.isnot(None))
        .first()
    )


@api_bp.route("/messages/<int:message_id>/assign", methods=["POST"])
@login_required
@require_perm("editDrafts")
def assign_message(message_id: int):
    """Assign a mail to a teammate (and give them a linked task)."""
    msg = get_message(message_id)
    raw = _payload().get("user_id")
    if raw in (None, "", "0", 0):
        msg.assignee_user_id = None
        db.session.commit()
        return ok(toast=_("Toewijzing verwijderd."), reload=True)
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        return ok(error=_("onbekend")), 400
    user = db.session.get(User, uid)
    if not user or user.organization_id != current_user.organization_id:
        return ok(error=_("Onbekende gebruiker.")), 404
    acct = msg.account
    if acct and acct.is_shared and uid not in acct.member_user_ids:
        return ok(error=_("Die gebruiker is geen lid van dit postvak.")), 400

    msg.assignee_user_id = uid
    # Collaboration ↔ tasks: ensure the assignee has a task for this mail.
    task = (
        db.session.query(Task)
        .filter_by(organization_id=msg.organization_id, linked_message_id=msg.id)
        .first()
    )
    if task is None:
        from app.tasks.task_ops import create_task as create_org_task

        data = {}
        default_list = _default_todo_list(user)
        if default_list is not None:
            data["todo_list_id"] = default_list.id  # land it in *their* Microsoft To Do
        create_org_task(user=user, title=msg.subject, message=msg, data=data)
    elif not task.done:
        task.user_id = uid  # hand the open task to the new assignee
    db.session.commit()

    from app.tasks.teams_notify import notify_assignment

    notify_assignment(msg, user.name)
    return ok(toast=_("Toegewezen aan %(name)s.", name=user.name), reload=True)


@api_bp.route("/messages/<int:message_id>/state", methods=["POST"])
@login_required
@require_perm("editDrafts")
def set_state(message_id: int):
    msg = get_message(message_id)
    state = (_payload().get("state") or "").strip()
    if state not in ("open", "in_progress", "done"):
        return ok(error=_("onbekende status")), 400
    msg.state = state
    db.session.commit()
    return ok(toast=_("Status bijgewerkt."), reload=True)

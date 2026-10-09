"""Opt-in daily task-digest email.

Once a day (from DAILY_EMAIL_HOUR in APP_TIMEZONE) each user who turned the daily email
on gets a summary of their open tasks that are overdue, due today, or have a reminder
today. Driven by the in-app scheduler (under its leader lock) and by Celery beat — both
call `send_daily_task_emails`, which self-gates on the hour and a per-user last-sent date
so a user is emailed at most once per day regardless of how often the tick fires.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from flask import current_app
from markupsafe import escape
from sqlalchemy import or_

from app.email import send_email
from app.extensions import db
from app.models import Task, User


def _mailer_ready() -> bool:
    cfg = current_app.config
    return bool((cfg.get("MAIL_PROVIDER") == "graph" and cfg.get("GRAPH_MAIL_CLIENT_ID"))
                or cfg.get("SMTP_HOST"))


def _local_now() -> datetime:
    from zoneinfo import ZoneInfo

    name = current_app.config.get("APP_TIMEZONE") or "Europe/Brussels"
    try:
        tz = ZoneInfo(name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Europe/Brussels")
    return datetime.now(tz)


def _todays_tasks(user: User, today) -> list[Task]:
    """Open tasks that are overdue, due today, or have a reminder up to end of today."""
    tomorrow = datetime.combine(today + timedelta(days=1), time.min)
    return (
        db.session.query(Task)
        .filter(Task.user_id == user.id, Task.done.is_(False))
        .filter(or_(Task.due_date <= today, Task.reminder_at < tomorrow))
        .order_by(Task.due_date.is_(None), Task.due_date)
        .all()
    )


def _render_digest(user: User, tasks: list[Task], today) -> tuple[str, str, str]:
    from app.htmltext import html_to_text

    overdue = [t for t in tasks if t.due_date and t.due_date < today]
    rest = [t for t in tasks if t not in overdue]
    # Built from config, not url_for: the digest runs in a background app-context with
    # no request, where url_for can't build an external URL.
    link = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/") + "/app/tasks"
    subject = f"Je taken voor vandaag — {len(tasks)} open"

    def _row(t: Task) -> str:
        bits = []
        if t.due_date and t.due_date < today:
            bits.append(f"<span style='color:#E2483D;font-weight:700'>te laat · {t.due_date.strftime('%d-%m')}</span>")
        elif t.due_date:
            bits.append(f"<span style='color:#6B6B78'>{t.due_date.strftime('%d-%m')}</span>")
        if t.assignee_name:
            bits.append(f"<span style='color:#2D6BDB'>👤 {escape(t.assignee_name)}</span>")
        meta = (" · ".join(bits))
        meta_html = f"<div style='font-size:12px;color:#6B6B78'>{meta}</div>" if meta else ""
        return (f"<li style='margin:0 0 10px'><strong>{escape(t.title)}</strong>{meta_html}</li>")

    def _section(title: str, items: list[Task]) -> str:
        if not items:
            return ""
        rows = "".join(_row(t) for t in items)
        return f"<p style='margin:18px 0 6px;font-weight:700;color:#1C142E'>{title}</p><ul style='padding-left:18px;margin:0'>{rows}</ul>"

    html = (
        "<div style='font-family:sans-serif;max-width:520px'>"
        f"<h2 style='color:#6C4CE6'>Goeiemorgen{(', ' + escape(user.name.split()[0])) if user.name else ''} 👋</h2>"
        f"<p>Dit staat er vandaag op je lijst ({len(tasks)} open):</p>"
        f"{_section('Te laat', overdue)}"
        f"{_section('Vandaag', rest)}"
        f"<p style='margin-top:22px'><a href='{link}' style='display:inline-block;background:#6C4CE6;color:#fff;"
        "padding:11px 20px;border-radius:11px;text-decoration:none;font-weight:700'>Open je taken</a></p>"
        "<p style='color:#9a9aa6;font-size:12px;margin-top:18px'>Je ontvangt deze mail omdat de dagelijkse takenmail "
        "aanstaat. Zet hem uit bij Instellingen.</p></div>"
    )
    return subject, html, html_to_text(html)


def send_daily_task_emails(now: datetime | None = None) -> int:
    """Email each opted-in user their task digest, at most once per day. Returns count sent."""
    if not _mailer_ready():
        return 0
    local = now or _local_now()
    if local.hour < current_app.config.get("DAILY_EMAIL_HOUR", 7):
        return 0
    today = local.date()
    sent = 0
    users = db.session.query(User).filter(User.daily_email_enabled.is_(True)).all()
    for user in users:
        if user.daily_email_sent_on == today or not (user.email and user.email_verified):
            continue
        try:
            tasks = _todays_tasks(user, today)
            if tasks:  # nothing due → no mail, but still stamp so we don't recheck all day
                subject, html, text = _render_digest(user, tasks, today)
                send_email(user.email, subject, html, text, template_key="daily_tasks")
                sent += 1
            user.daily_email_sent_on = today
            db.session.commit()
        except Exception:  # noqa: BLE001 — one bad user must not stall the rest
            db.session.rollback()
            current_app.logger.exception("[digest] daily email failed for user %s", user.id)
    return sent

"""Opt-in daily task-digest email: toggle, scheduling gates, selection, dedupe."""
from datetime import date, datetime

from app.extensions import db
from app.models import Task, User
from app.seed.demo_data import DEMO_PASSWORD
from app.tasks import digest


def _login(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _demo() -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _ready(app, monkeypatch):
    monkeypatch.setitem(app.config, "SMTP_HOST", "smtp.test")  # mailer "ready"


def _task(user, title, **kw):
    db.session.add(Task(organization_id=user.organization_id, user_id=user.id, title=title,
                        done=kw.pop("done", False), **kw))
    db.session.commit()


# ── Toggle ────────────────────────────────────────────────────────────────────
def test_toggle_daily_email(app, client):
    _login(client)
    assert _demo().daily_email_enabled is False
    r = client.post("/api/settings/daily-email/toggle").get_json()
    assert r["ok"] and r["on"] is True
    db.session.expire_all()
    assert _demo().daily_email_enabled is True
    assert client.post("/api/settings/daily-email/toggle").get_json()["on"] is False


def test_settings_page_shows_daily_email_toggle(app, client):
    _login(client)
    html = client.get("/app/settings").get_data(as_text=True)
    assert "Dagelijkse takenmail" in html and "toggle-daily-email" in html


# ── Scheduling gates ──────────────────────────────────────────────────────────
def test_digest_noop_without_mailer(app, monkeypatch):
    monkeypatch.setitem(app.config, "SMTP_HOST", None)
    monkeypatch.setitem(app.config, "MAIL_PROVIDER", "smtp")
    assert digest.send_daily_task_emails(datetime(2026, 7, 1, 9, 0)) == 0


def test_digest_noop_before_hour(app, monkeypatch):
    _ready(app, monkeypatch)
    u = _demo()
    u.daily_email_enabled = True
    u.email_verified = True
    _task(u, "iets", due_date=date(2026, 7, 1))
    assert digest.send_daily_task_emails(datetime(2026, 7, 1, 5, 0)) == 0  # before DAILY_EMAIL_HOUR


# ── Send + dedupe ─────────────────────────────────────────────────────────────
def test_digest_sends_and_dedupes(app, monkeypatch):
    _ready(app, monkeypatch)
    sends = []
    monkeypatch.setattr(digest, "send_email",
                        lambda to, subj, html, text, template_key=None: sends.append((to, subj)) or True)
    u = _demo()
    u.daily_email_enabled = True
    u.email_verified = True
    _task(u, "Vandaag te doen", due_date=date(2026, 7, 1))

    n = digest.send_daily_task_emails(datetime(2026, 7, 1, 9, 0))
    assert n == 1 and sends and sends[0][0] == u.email
    db.session.expire_all()
    assert _demo().daily_email_sent_on == date(2026, 7, 1)

    sends.clear()  # same day again → no resend
    assert digest.send_daily_task_emails(datetime(2026, 7, 1, 9, 0)) == 0 and sends == []


def test_digest_skips_disabled_and_unverified(app, monkeypatch):
    _ready(app, monkeypatch)
    sends = []
    monkeypatch.setattr(digest, "send_email", lambda *a, **k: sends.append(a) or True)
    u = _demo()
    u.daily_email_enabled = False  # off
    u.email_verified = True
    _task(u, "x", due_date=date(2026, 7, 1))
    assert digest.send_daily_task_emails(datetime(2026, 7, 1, 9, 0)) == 0 and sends == []


# ── Task selection ────────────────────────────────────────────────────────────
def test_todays_tasks_selection(app):
    u = _demo()
    today = date(2026, 7, 1)
    _task(u, "overdue", due_date=date(2026, 6, 29))
    _task(u, "today", due_date=today)
    _task(u, "future", due_date=date(2026, 7, 10))
    _task(u, "done-today", due_date=today, done=True)
    _task(u, "reminder-today", reminder_at=datetime(2026, 7, 1, 9, 0))
    titles = {t.title for t in digest._todays_tasks(u, today)}
    assert {"overdue", "today", "reminder-today"} <= titles
    assert "future" not in titles and "done-today" not in titles

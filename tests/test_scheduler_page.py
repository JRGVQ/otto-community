"""Owner backoffice: the scheduler & mailbox-sync check page + manual sync."""
from datetime import timedelta

from app.extensions import db
from app.models import MailAccount, User
from app.models.base import utcnow
from app.models.scheduler import SchedulerLock
from app.seed.demo_data import DEMO_PASSWORD


def _login_owner(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _demo_user() -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_scheduler_page_requires_platform_owner(client):
    # Not logged in as owner → the blueprint guard redirects to login.
    resp = client.get("/owner/scheduler")
    assert resp.status_code in (302, 403)


def test_scheduler_page_renders(client):
    _login_owner(client)
    resp = client.get("/owner/scheduler")
    assert resp.status_code == 200
    assert "Scheduler" in resp.get_data(as_text=True)


def test_verdict_flags_disabled_background_sync(client, app):
    _login_owner(client)
    # In TESTING BACKGROUND_SYNC is off and no realtime webhook → critical verdict.
    resp = client.get("/owner/scheduler")
    assert "nieuwe mail wordt nooit opgehaald" in resp.get_data(as_text=True)
    assert "BACKGROUND_SYNC=1" in resp.get_data(as_text=True)


def test_poller_alive_from_fresh_lease(client, app):
    _login_owner(client)
    lease = max(int(app.config.get("SCHEDULER_TICK_SECONDS", 60)), 15) * 3
    db.session.merge(SchedulerLock(
        name="mailbox-sync", holder="host:1",
        expires_at=utcnow() + timedelta(seconds=lease),  # just renewed → alive
    ))
    db.session.commit()
    resp = client.get("/owner/scheduler")
    assert "Actief" in resp.get_data(as_text=True)


def test_error_mailbox_surfaces_last_error(client, app):
    _login_owner(client)
    user = _demo_user()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="kapot@acme.be", status="error",
                       last_error="HTTP 401 — InvalidAuthenticationToken")
    db.session.add(acct)
    db.session.commit()
    resp = client.get("/owner/scheduler")
    body = resp.get_data(as_text=True)
    assert "kapot@acme.be" in body
    assert "InvalidAuthenticationToken" in body


def test_manual_sync_claims_and_redirects(client, app):
    _login_owner(client)
    user = _demo_user()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="pull@acme.be", status="connected",
                       last_sync_at=utcnow() - timedelta(hours=2))
    db.session.add(acct)
    db.session.commit()
    resp = client.post("/owner/scheduler/sync", follow_redirects=False)
    assert resp.status_code == 302
    # Eager sync runs inline (unconfigured provider → no-op) and claims the timestamp.
    refreshed = db.session.get(MailAccount, acct.id)
    assert refreshed.last_sync_at > utcnow() - timedelta(minutes=1)

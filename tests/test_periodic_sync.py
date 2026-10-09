"""Periodic mailbox sync: interval-aware scheduling, leader lock, manual refresh."""
from datetime import timedelta

from app.extensions import db
from app.models import MailAccount, Message, User
from app.models.base import utcnow
from app.providers.dto import MessageDTO
from app.models.scheduler import SchedulerLock, acquire_lock
from app.seed.demo_data import DEMO_PASSWORD
from app.tasks import scheduler


def _demo_user() -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _account(user, *, last_sync_at=None, interval=None, status="connected") -> MailAccount:
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="x@acme.be", status=status,
                       last_sync_at=last_sync_at, sync_interval_seconds=interval)
    db.session.add(acct)
    db.session.commit()
    return acct


def _quiesce_existing():
    """Mark every seeded mailbox as just-synced so only the test's accounts are due."""
    for acct in db.session.query(MailAccount):
        acct.last_sync_at = utcnow()
    db.session.commit()


# ── Background gating ─────────────────────────────────────────────────────────
def test_scheduler_disabled_in_testing(app):
    assert app.config["BACKGROUND_SYNC"] is False  # no poller thread in the test process


def test_start_background_sync_is_noop_when_disabled(app):
    # Must return cleanly without spawning a thread when BACKGROUND_SYNC is off.
    scheduler.start_background_sync(app)
    assert scheduler._started is False


# ── Interval-aware due selection ──────────────────────────────────────────────
def test_due_skips_recently_synced(app, monkeypatch):
    _quiesce_existing()
    user = _demo_user()
    now = utcnow()
    monkeypatch.setitem(app.config, "SYNC_INTERVAL_SECONDS", 300)

    fresh = _account(user, last_sync_at=now - timedelta(seconds=100))   # within interval → skip
    stale = _account(user, last_sync_at=now - timedelta(seconds=400))   # past interval → due
    never = _account(user, last_sync_at=None)                            # never synced → due

    due_ids = {a.id for a in scheduler.due_accounts(now)}
    assert fresh.id not in due_ids
    assert {stale.id, never.id} <= due_ids


def test_due_honours_per_account_interval(app):
    _quiesce_existing()
    user = _demo_user()
    now = utcnow()
    last = now - timedelta(seconds=200)
    quick = _account(user, last_sync_at=last, interval=120)   # 200s > 120s → due
    slow = _account(user, last_sync_at=last, interval=600)    # 200s < 600s → not due

    due_ids = {a.id for a in scheduler.due_accounts(now)}
    assert quick.id in due_ids
    assert slow.id not in due_ids


def test_interval_floor_applies(app, monkeypatch):
    monkeypatch.setitem(app.config, "SYNC_MIN_INTERVAL_SECONDS", 60)
    user = _demo_user()
    acct = _account(user, interval=5)  # below the floor
    assert scheduler._interval_for(acct) == 60


# ── run_due_syncs: claim-then-enqueue ─────────────────────────────────────────
def test_run_due_syncs_enqueues_and_claims(app, monkeypatch):
    _quiesce_existing()
    user = _demo_user()
    now = utcnow()
    due = _account(user, last_sync_at=now - timedelta(seconds=900))

    calls = []
    monkeypatch.setattr(scheduler, "enqueue", lambda func, *a: calls.append((func.__name__, a)))

    n = scheduler.run_due_syncs()
    assert n == 1
    assert ("sync_account", (due.id,)) in calls
    # The due timestamp is claimed up front so the next tick won't re-enqueue it.
    db.session.refresh(due)
    assert due.last_sync_at is not None
    assert not scheduler.due_accounts()  # nothing due immediately afterwards


# ── Leader lock ───────────────────────────────────────────────────────────────
def test_leader_lock_single_holder(app):
    assert acquire_lock("sync", ttl_seconds=100, holder="worker-A") is True
    assert acquire_lock("sync", ttl_seconds=100, holder="worker-B") is False  # A still holds it
    assert acquire_lock("sync", ttl_seconds=100, holder="worker-A") is True   # holder may renew


def test_leader_lock_steals_expired_lease(app):
    assert acquire_lock("sync", ttl_seconds=100, holder="worker-A") is True
    lock = db.session.get(SchedulerLock, "sync")
    lock.expires_at = utcnow() - timedelta(seconds=1)  # force the lease to expire
    db.session.commit()
    assert acquire_lock("sync", ttl_seconds=100, holder="worker-B") is True  # stale lease stolen
    assert db.session.get(SchedulerLock, "sync").holder == "worker-B"


# ── Manual refresh endpoint ───────────────────────────────────────────────────
def test_manual_sync_reports_new_mail(app, client, monkeypatch):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    monkeypatch.setattr("app.tasks.sync.sync_account", lambda account_id: 3)

    r = client.post("/api/accounts/sync")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body.get("reload") is True
    assert "3 nieuwe" in body["toast"]


def test_manual_sync_no_new_mail(app, client, monkeypatch):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    monkeypatch.setattr("app.tasks.sync.sync_account", lambda account_id: 0)

    r = client.post("/api/accounts/sync")
    body = r.get_json()
    assert body["ok"] and "reload" not in body
    assert "geen nieuwe mail" in body["toast"].lower()


def test_manual_sync_without_mailbox(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    for acct in db.session.query(MailAccount).filter_by(user_id=_demo_user().id):
        db.session.delete(acct)
    db.session.commit()

    r = client.post("/api/accounts/sync")
    assert "Geen gekoppeld postvak" in r.get_json()["toast"]


# ── Disconnect a mailbox (the "Loskoppelen" button) ───────────────────────────
def test_disconnect_removes_mailbox(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    aid = _account(_demo_user()).id
    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 200 and r.get_json().get("ok")
    assert db.session.get(MailAccount, aid) is None  # tokens + cached mail cascade-removed


def test_disconnect_rejects_another_users_mailbox(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    other = db.session.query(User).filter(User.id != _demo_user().id).first()
    if other is None:
        return  # single-user demo → nothing to assert
    aid = _account(other).id
    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 404
    assert db.session.get(MailAccount, aid) is not None  # someone else's mailbox is untouched


def test_integrations_shows_disconnect_button(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    _account(_demo_user())
    html = client.get("/app/integrations").get_data(as_text=True)
    assert "Loskoppelen" in html
    assert 'data-action="disconnect-mailbox"' in html


# ── Settings: configure the interval ──────────────────────────────────────────
def test_save_sync_interval(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    r = client.post("/app/settings/synchronisatie", data={"sync_interval": "900"},
                    follow_redirects=False)
    assert r.status_code == 302
    accts = db.session.query(MailAccount).filter_by(user_id=_demo_user().id, status="connected").all()
    assert accts and all(a.sync_interval_seconds == 900 for a in accts)


def test_save_sync_interval_rejects_unknown_value(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    before = [a.sync_interval_seconds for a in
              db.session.query(MailAccount).filter_by(user_id=_demo_user().id)]
    r = client.post("/app/settings/synchronisatie", data={"sync_interval": "7"},
                    follow_redirects=False)
    assert r.status_code == 302  # rejected, redirected, nothing changed
    after = [a.sync_interval_seconds for a in
             db.session.query(MailAccount).filter_by(user_id=_demo_user().id)]
    assert before == after


def test_settings_page_shows_sync_card(app, client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    html = client.get("/app/settings").get_data(as_text=True)
    assert "Synchronisatie" in html
    assert "Nu synchroniseren" in html


# ── sync_cursor must hold a full Graph deltaLink (not VARCHAR(255)) ────────────
# Regression guard for the bug where a deltaLink (folder-id + $deltatoken, 350-500
# chars) overflowed sync_cursor's VARCHAR(255): silent on SQLite, but "value too long"
# on Postgres, which sync_account swallowed + rolled back — so every Outlook sync
# fetched nothing and the inbox never updated.

def test_sync_cursor_column_is_unbounded():
    col = MailAccount.__table__.c.sync_cursor
    assert getattr(col.type, "length", None) is None  # Text, not String(N)


def _stub_provider(monkeypatch, messages, cursor):
    from app.tasks import sync as sync_mod

    class _FakeProvider:
        def is_configured(self):
            return True

        def fetch_new(self, _cursor):
            return messages, cursor

    monkeypatch.setattr(sync_mod, "get_provider", lambda account: _FakeProvider())
    monkeypatch.setattr(sync_mod, "enqueue", lambda *a, **k: None)  # don't run the pipeline
    monkeypatch.setattr(sync_mod, "_maybe_pull_todo", lambda account: None)
    return sync_mod


def _long_delta_link() -> str:
    link = ("https://graph.microsoft.com/v1.0/me/mailFolders/" + "A" * 160
            + "/messages/delta?$deltatoken=" + "Z" * 220)
    assert len(link) > 255
    return link


def _dto(mid: str) -> MessageDTO:
    return MessageDTO(provider_message_id=mid, thread_id="t1", sender="Klant",
                      sender_email="klant@x.be", subject="Nieuwe vraag",
                      snippet="...", body="hallo")


def test_sync_account_persists_long_delta_cursor(app, monkeypatch):
    user = _demo_user()
    acct = _account(user)
    cursor = _long_delta_link()
    sync_mod = _stub_provider(monkeypatch, [_dto("msg-long-1")], cursor)

    count = sync_mod.sync_account(acct.id)

    assert count == 1
    db.session.refresh(acct)
    assert acct.sync_cursor == cursor  # stored in full — never truncated or lost
    assert db.session.query(Message).filter_by(provider_message_id="msg-long-1").first()


def test_sync_account_keeps_mail_when_cursor_commit_fails(app, monkeypatch):
    # Hardening: the fetched mail is committed BEFORE the cursor, so a cursor-commit
    # failure can no longer roll the new messages back out (the original silent dataloss).
    user = _demo_user()
    acct = _account(user)
    sync_mod = _stub_provider(monkeypatch, [_dto("msg-hard-1")], _long_delta_link())

    real_commit = db.session.commit
    calls = {"n": 0}

    # sync_account commit in deze volgorde:
    #   1. release_db_connection() — geeft de verbinding vrij vóór de provider-call
    #   2. de opgehaalde berichten
    #   3. de cursor, bewust apart zodat een fout hier de import niet terugdraait
    # We laten (3) falen.
    CURSOR_COMMIT = 3

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == CURSOR_COMMIT:
            raise RuntimeError("simulated cursor commit failure")
        return real_commit()

    monkeypatch.setattr(db.session, "commit", flaky_commit)
    sync_mod.sync_account(acct.id)  # swallows the failure, logs a warning
    monkeypatch.undo()

    # The message from the first commit survived despite the cursor failure.
    assert db.session.query(Message).filter_by(provider_message_id="msg-hard-1").first()


def test_initial_import_swallows_provider_failure(app, monkeypatch):
    # Regression: the handler was `except (ProviderError, Exception)` but ProviderError
    # isn't imported in sync.py, so it raised NameError and masked the real error instead
    # of returning 0. A failing first-connect import must degrade quietly.
    from app.tasks import sync as sync_mod

    user = _demo_user()
    acct = _account(user)

    class _BoomProvider:
        def is_configured(self):
            return True

        def list_recent(self, limit=300):
            raise RuntimeError("Graph unreachable")

    monkeypatch.setattr(sync_mod, "get_provider", lambda account: _BoomProvider())

    assert sync_mod.initial_import(acct.id) == 0  # no NameError, just a clean 0


# ── Mock (demo/seed) mailboxes ────────────────────────────────────────────────
def test_mock_mailboxes_are_never_due(app):
    """A demo/seed mailbox is flagged 'connected' so the demo looks real, but there is
    no Graph grant behind it. Polling one only ever logged "Geen geldige Graph-token"
    and flipped it to 'error' — and every demo visit creates one."""
    _quiesce_existing()
    user = _demo_user()
    now = utcnow()

    real = _account(user, last_sync_at=None)
    mock = _account(user, last_sync_at=None)
    mock.provider_config = {"demo": True}
    db.session.commit()

    due_ids = {a.id for a in scheduler.due_accounts(now)}
    assert real.id in due_ids
    assert mock.id not in due_ids


def test_seeded_demo_mailbox_is_not_polled(app):
    _quiesce_existing()
    seeded = db.session.query(MailAccount).filter_by(email="demo@example.com").first()
    seeded.last_sync_at = None
    db.session.commit()

    assert seeded.is_mock
    assert seeded.id not in {a.id for a in scheduler.due_accounts()}


def test_sync_account_skips_a_mock_mailbox(app):
    """Belt and braces for the paths that bypass the scheduler (manual refresh,
    webhooks). With platform Graph credentials present — as in production — the provider
    calls a demo mailbox 'configured' and then finds no token behind it, so every poll
    logged "Geen geldige Graph-token" and parked the demo mailbox in 'error'."""
    from app.tasks.sync import sync_account

    app.config["GRAPH_CONFIGURED"] = True
    mock = db.session.query(MailAccount).filter_by(email="demo@example.com").first()
    assert mock.is_mock

    assert sync_account(mock.id) == 0
    db.session.refresh(mock)
    assert mock.status == "connected"
    assert mock.last_error is None


def test_sync_heals_a_mock_mailbox_parked_in_error(app):
    """Mailboxes the old behaviour already flipped to 'error' would stay that way, since
    nothing polls them any more. A manual refresh (which retries 'error' mailboxes)
    clears the stale failure."""
    from app.tasks.sync import sync_account

    user = _demo_user()
    mock = _account(user, status="error")
    mock.provider_config = {"seed": True}
    mock.last_error = "ProviderAuthError: Geen geldige Graph-token; koppel het account opnieuw."
    db.session.commit()

    assert sync_account(mock.id) == 0
    db.session.refresh(mock)
    assert mock.status == "connected"
    assert mock.last_error is None

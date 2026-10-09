"""Realtime (Graph change-notification subscription) wiring — gated and safe.

The whole realtime path must stay OFF by default (eager/no client-state), and the
webhook must only act on authentic notifications for a known subscription.
"""
from app.extensions import db
from app.models import MailAccount, User
from app.providers.outlook import OutlookProvider


def _outlook_account() -> MailAccount:
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    acct = MailAccount(user_id=user.id, provider="outlook", email="demo@acme.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


# ── Gating ────────────────────────────────────────────────────────────────────
def test_realtime_gate_off_in_testing(app):
    assert app.config.get("GRAPH_REALTIME") is False  # eager + no client state → polling only


def test_start_watch_noop_when_disabled(app, monkeypatch):
    acct = _outlook_account()
    p = OutlookProvider(acct)
    called = []
    monkeypatch.setattr(p, "_post", lambda path, payload=None: called.append(path) or {})
    p.start_watch()
    assert called == [] and acct.subscription_id is None  # gated → no subscription created


# ── Subscription creation (forced on) ─────────────────────────────────────────
def test_start_watch_creates_subscription_when_enabled(app, monkeypatch):
    acct = _outlook_account()
    monkeypatch.setitem(app.config, "GRAPH_REALTIME", True)
    monkeypatch.setitem(app.config, "GRAPH_CLIENT_STATE", "secret-state")
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://app.example.com")

    p = OutlookProvider(acct)
    captured = {}

    def post(path, payload=None):
        captured["path"], captured["payload"] = path, payload
        return {"id": "sub-123"}

    monkeypatch.setattr(p, "_post", post)
    p.start_watch()

    assert captured["path"] == "/subscriptions"
    pl = captured["payload"]
    assert pl["resource"] == "me/mailFolders('inbox')/messages"
    assert pl["changeType"] == "created"
    assert pl["clientState"] == "secret-state"
    assert pl["notificationUrl"] == "https://app.example.com/api/webhooks/microsoft"
    assert acct.subscription_id == "sub-123" and acct.watch_expiry is not None


# ── Webhook endpoint ──────────────────────────────────────────────────────────
def test_microsoft_webhook_validation_echo(app, client):
    r = client.post("/api/webhooks/microsoft?validationToken=abc123")
    assert r.status_code == 200 and r.data.decode() == "abc123" and r.mimetype == "text/plain"


def test_microsoft_webhook_enqueues_sync_for_matching_subscription(app, client, monkeypatch):
    acct = _outlook_account()
    acct.subscription_id = "sub-xyz"
    db.session.commit()
    monkeypatch.setitem(app.config, "GRAPH_CLIENT_STATE", "the-state")

    recorded = []
    monkeypatch.setattr("app.tasks.enqueue.enqueue",
                        lambda func, *a, **k: recorded.append((func.__name__, a)))

    r = client.post("/api/webhooks/microsoft",
                    json={"value": [{"subscriptionId": "sub-xyz", "clientState": "the-state"}]})
    assert r.status_code == 202
    assert recorded == [("sync_account", (acct.id,))]


def test_microsoft_webhook_rejects_bad_clientstate(app, client, monkeypatch):
    acct = _outlook_account()
    acct.subscription_id = "sub-zzz"
    db.session.commit()
    monkeypatch.setitem(app.config, "GRAPH_CLIENT_STATE", "the-state")

    recorded = []
    monkeypatch.setattr("app.tasks.enqueue.enqueue", lambda *a, **k: recorded.append(a))

    r = client.post("/api/webhooks/microsoft",
                    json={"value": [{"subscriptionId": "sub-zzz", "clientState": "WRONG"}]})
    assert r.status_code == 202 and recorded == []  # authenticity check rejected → no work

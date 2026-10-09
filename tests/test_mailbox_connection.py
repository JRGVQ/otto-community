"""The per-mailbox connection test surfaces the provider's real error (with code) and
records it on the account, instead of a blind 'OK' / 'no new mail'."""
import urllib.error

from app.extensions import db
from app.models import MailAccount
from app.providers.exceptions import ProviderAuthError


def _account():
    return db.session.query(MailAccount).first()  # the seeded demo mailbox (demo user's)


def test_connection_test_reports_http_error_with_code(auth_client, monkeypatch):
    acct = _account()

    class Forbidden:
        def is_configured(self):
            return True

        def list_recent(self, limit=1):
            raise urllib.error.HTTPError("https://graph", 403, "Forbidden", {}, None)

    monkeypatch.setattr("app.providers.get_provider", lambda a: Forbidden())
    r = auth_client.post(f"/api/accounts/{acct.id}/test").get_json()

    assert r["connection_ok"] is False
    assert "403" in (r.get("error") or "")
    db.session.refresh(acct)
    assert acct.last_error and "403" in acct.last_error


def test_auth_error_flips_status_then_a_healthy_probe_clears_it(auth_client, monkeypatch):
    acct = _account()

    class Expired:
        def is_configured(self):
            return True

        def list_recent(self, limit=1):
            raise ProviderAuthError("Geen geldige Graph-token; koppel het account opnieuw.")

    monkeypatch.setattr("app.providers.get_provider", lambda a: Expired())
    r = auth_client.post(f"/api/accounts/{acct.id}/test").get_json()
    assert r["connection_ok"] is False
    db.session.refresh(acct)
    assert acct.status == "error" and acct.last_error  # auth failure → reconnect state

    class Healthy:
        def is_configured(self):
            return True

        def list_recent(self, limit=1):
            return []

    monkeypatch.setattr("app.providers.get_provider", lambda a: Healthy())
    r2 = auth_client.post(f"/api/accounts/{acct.id}/test").get_json()
    assert r2["connection_ok"] is True
    db.session.refresh(acct)
    assert acct.status == "connected" and acct.last_error is None


def test_cannot_test_another_users_mailbox(auth_client, app):
    # An account that doesn't belong to the logged-in user must 404.
    acct = _account()
    # Point the account at a different user id to simulate someone else's mailbox.
    real_owner = acct.user_id
    acct.user_id = real_owner + 9999
    db.session.commit()
    assert auth_client.post(f"/api/accounts/{acct.id}/test").status_code == 404
    acct.user_id = real_owner
    db.session.commit()

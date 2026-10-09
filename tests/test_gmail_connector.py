"""Gmail connector: per-org credentials, token persistence and the users.watch half.

Three things were broken here, and each made Gmail look "niet werkend" in a different way:

* `is_configured()` read the *platform* credentials, so a customer on its own Google app
  connected fine and then failed every sync with "Gmail is niet geconfigureerd".
* the credentials were built from the platform client id/secret too, so refreshing a BYO
  grant was rejected by Google — the mailbox died one access token later — and on a
  deployment without platform credentials the lookup raised KeyError, i.e. a 500.
* nothing ever called `users.watch`, so Google never pushed anything: realtime silently
  did not exist, however carefully the webhook end was configured.

Google's libraries are optional (commented out in requirements.txt, absent in CI), so the
SDK surface the provider touches is faked — the same approach as test_gmail_push.py.
These tests are about Otto's own logic: which client is used, what is persisted, and
whether a mailbox actually gets watched.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime

import pytest

from app.extensions import db
from app.models import MailAccount
from app.providers.exceptions import ProviderAuthError, ProviderNotConfigured
from app.providers.gmail import GmailProvider
from app.security.crypto import decrypt, encrypt

TOPIC = "projects/otto-prod/topics/gmail-push"
NEW_EXPIRY = datetime(2030, 1, 1, 12, 0, 0)


# ── Fakes ────────────────────────────────────────────────────────────────────
@pytest.fixture()
def google_sdk(monkeypatch):
    """Stand in for google-auth / google-api-python-client. Returns a knob object:
    `sdk.created` are the Credentials built, `sdk.refresh_error` makes a refresh fail,
    `sdk.calls` records the Gmail API calls the provider made."""
    sdk = types.SimpleNamespace(created=[], refresh_error=None, rotated_refresh_token=None, calls={})

    class FakeCredentials:
        def __init__(self, token=None, refresh_token=None, token_uri=None,
                     client_id=None, client_secret=None, scopes=None):
            self.token, self.refresh_token, self.token_uri = token, refresh_token, token_uri
            self.client_id, self.client_secret, self.scopes = client_id, client_secret, scopes
            self.expiry, self.expired = None, False
            sdk.created.append(self)

        def refresh(self, request):
            if sdk.refresh_error:
                raise sdk.refresh_error
            self.token, self.expiry, self.expired = "fresh-access-token", NEW_EXPIRY, False
            if sdk.rotated_refresh_token:
                self.refresh_token = sdk.rotated_refresh_token

    class FakeUsers:
        def watch(self, userId, body):
            sdk.calls["watch"] = {"userId": userId, "body": body}
            return types.SimpleNamespace(
                execute=lambda: {"historyId": "424242", "expiration": "1800000000000"})

        def stop(self, userId):
            sdk.calls["stop"] = {"userId": userId}
            return types.SimpleNamespace(execute=lambda: {})

    modules = {
        "google": types.ModuleType("google"),
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.credentials": types.ModuleType("google.oauth2.credentials"),
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": types.ModuleType("google.auth.transport.requests"),
        "googleapiclient": types.ModuleType("googleapiclient"),
        "googleapiclient.discovery": types.ModuleType("googleapiclient.discovery"),
    }
    modules["google.oauth2.credentials"].Credentials = FakeCredentials
    modules["google.auth.transport.requests"].Request = lambda *a, **k: object()
    modules["googleapiclient.discovery"].build = (
        lambda *a, **k: types.SimpleNamespace(users=FakeUsers))
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return sdk


@pytest.fixture()
def gmail_account(app):
    """A connected Gmail mailbox on a fresh org, carrying a stored (fake) grant."""
    from app.auth.service import create_company

    user = create_company("Gmail BV", "Gwen", "gwen@gmailbv.be", "geheim123")
    account = MailAccount(
        user_id=user.id, organization_id=user.organization_id, provider="gmail",
        email="gwen@gmailbv.be", status="connected",
        access_token_enc=encrypt("stale-access-token"),
        refresh_token_enc=encrypt("the-refresh-token"),
    )
    db.session.add(account)
    db.session.commit()
    return account


def _byo_google(account, client_id="byo-client-id", secret="byo-secret"):
    """Give this mailbox's org its own Google app (the Connect plan's BYO setup)."""
    org = account.user.organization
    org.google_client_id, org.google_client_secret_enc = client_id, encrypt(secret)
    db.session.commit()


def _realtime(app, monkeypatch, topic=TOPIC):
    audience, sa = "https://otto.example.com/api/webhooks/gmail", "otto-push@p.iam.gserviceaccount.com"
    monkeypatch.setitem(app.config, "GMAIL_PUBSUB_TOPIC", topic)
    monkeypatch.setitem(app.config, "GMAIL_PUSH_AUDIENCE", audience)
    monkeypatch.setitem(app.config, "GMAIL_PUSH_SA_EMAIL", sa)
    # app.config is a flat snapshot taken at boot, so the derived flag is set here the way
    # BaseConfig.GMAIL_REALTIME derives it — the parametrised test at the bottom is what
    # guards that derivation itself.
    monkeypatch.setitem(app.config, "GMAIL_REALTIME", bool(topic and audience and sa))


# ── Per-org configuration ────────────────────────────────────────────────────
def test_org_with_its_own_google_app_is_configured(app, gmail_account, google_sdk):
    """This test config has no platform Google credentials — the org's own app carries it."""
    assert GmailProvider(gmail_account).is_configured() is False
    _byo_google(gmail_account)
    assert GmailProvider(gmail_account).is_configured() is True


def test_missing_sdk_reports_unconfigured_rather_than_crashing(app, gmail_account, monkeypatch):
    """Credentials fine, libraries absent. Simulated rather than assumed: the Google SDK
    is absent in CI but present wherever requirements-prod.txt is installed, and this
    behaviour must hold either way. (A None entry in sys.modules raises ImportError.)"""
    _byo_google(gmail_account)
    monkeypatch.setitem(sys.modules, "googleapiclient", None)
    assert GmailProvider(gmail_account).is_configured() is False


def test_credentials_use_the_org_app_that_issued_the_grant(app, gmail_account, google_sdk):
    _byo_google(gmail_account)
    creds = GmailProvider(gmail_account)._credentials()
    assert (creds.client_id, creds.client_secret) == ("byo-client-id", "byo-secret")
    assert creds.refresh_token == "the-refresh-token"


def test_credentials_fall_back_to_the_platform_app(app, gmail_account, google_sdk, monkeypatch):
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_ID", "platform-id")
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_SECRET", "platform-secret")
    creds = GmailProvider(gmail_account)._credentials()
    assert (creds.client_id, creds.client_secret) == ("platform-id", "platform-secret")


def test_missing_credentials_raise_instead_of_keyerror(app, gmail_account, google_sdk):
    """The old code indexed config["GOOGLE_CLIENT_ID"] — a KeyError, so a 500, on exactly
    the deployment where Gmail is reported as not configured."""
    with pytest.raises(ProviderNotConfigured):
        GmailProvider(gmail_account)._credentials()


def test_grant_without_tokens_is_an_auth_error(app, gmail_account, google_sdk):
    _byo_google(gmail_account)
    gmail_account.access_token_enc = gmail_account.refresh_token_enc = None
    db.session.commit()
    with pytest.raises(ProviderAuthError):
        GmailProvider(gmail_account)._credentials()


# ── Token refresh is persisted ───────────────────────────────────────────────
def test_refresh_stores_the_new_token_and_its_expiry(app, gmail_account, google_sdk):
    _byo_google(gmail_account)

    GmailProvider(gmail_account).refresh_token()

    db.session.refresh(gmail_account)
    assert decrypt(gmail_account.access_token_enc) == "fresh-access-token"
    assert gmail_account.token_expiry == NEW_EXPIRY
    # Rotation-safe: the refresh token is only replaced when Google hands back a new one.
    assert decrypt(gmail_account.refresh_token_enc) == "the-refresh-token"


def test_refresh_keeps_a_rotated_refresh_token(app, gmail_account, google_sdk):
    _byo_google(gmail_account)
    google_sdk.rotated_refresh_token = "rotated-refresh-token"

    GmailProvider(gmail_account).refresh_token()

    db.session.refresh(gmail_account)
    assert decrypt(gmail_account.refresh_token_enc) == "rotated-refresh-token"


def test_refresh_failure_surfaces_as_an_auth_error(app, gmail_account, google_sdk):
    _byo_google(gmail_account)
    google_sdk.refresh_error = RuntimeError("invalid_client")
    with pytest.raises(ProviderAuthError):
        GmailProvider(gmail_account).refresh_token()


def test_refresh_is_a_no_op_when_gmail_is_not_configured(app, gmail_account, google_sdk):
    GmailProvider(gmail_account).refresh_token()  # nothing to refresh — must not raise
    assert gmail_account.token_expiry is None


def test_service_refreshes_a_stale_token_before_using_it(app, gmail_account, google_sdk):
    """The whole point of persisting: a mailbox connected an hour ago keeps working."""
    _byo_google(gmail_account)
    provider = GmailProvider(gmail_account)
    original_credentials = provider._credentials

    def expired_credentials():
        creds = original_credentials()
        creds.expired = True
        return creds

    provider._credentials = expired_credentials
    provider._service()

    db.session.refresh(gmail_account)
    assert decrypt(gmail_account.access_token_enc) == "fresh-access-token"


# ── users.watch ──────────────────────────────────────────────────────────────
def test_start_watch_registers_the_mailbox_with_pubsub(app, gmail_account, google_sdk, monkeypatch):
    from app.timefmt import parse_epoch_ms

    _byo_google(gmail_account)
    _realtime(app, monkeypatch)

    GmailProvider(gmail_account).start_watch()

    assert google_sdk.calls["watch"]["body"]["topicName"] == TOPIC
    assert google_sdk.calls["watch"]["body"]["labelIds"] == ["INBOX"]
    db.session.refresh(gmail_account)
    assert gmail_account.subscription_id == TOPIC
    assert gmail_account.watch_expiry == parse_epoch_ms("1800000000000")


def test_start_watch_does_nothing_without_a_topic(app, gmail_account, google_sdk, monkeypatch):
    """Half-configured realtime stays off rather than watching onto nothing."""
    _byo_google(gmail_account)
    _realtime(app, monkeypatch, topic=None)

    GmailProvider(gmail_account).start_watch()

    assert "watch" not in google_sdk.calls and gmail_account.subscription_id is None


def test_a_failing_watch_degrades_to_polling(app, gmail_account, google_sdk, monkeypatch):
    _byo_google(gmail_account)
    _realtime(app, monkeypatch)
    monkeypatch.setattr(GmailProvider, "_service",
                        lambda self: (_ for _ in ()).throw(RuntimeError("topic not found")))

    GmailProvider(gmail_account).start_watch()  # logged, never raised

    assert gmail_account.subscription_id is None


def test_renewing_re_arms_the_watch(app, gmail_account, google_sdk, monkeypatch):
    """Gmail caps a watch at 7 days and extends it by calling watch() again."""
    _byo_google(gmail_account)
    _realtime(app, monkeypatch)
    gmail_account.subscription_id, gmail_account.watch_expiry = TOPIC, datetime(2020, 1, 1)
    db.session.commit()

    GmailProvider(gmail_account).renew_watch()

    assert google_sdk.calls["watch"]["body"]["topicName"] == TOPIC
    db.session.refresh(gmail_account)
    assert gmail_account.watch_expiry > datetime(2020, 1, 1)


def test_stop_watch_clears_the_registration(app, gmail_account, google_sdk):
    _byo_google(gmail_account)
    gmail_account.subscription_id = TOPIC
    db.session.commit()

    GmailProvider(gmail_account).stop_watch()

    assert google_sdk.calls["stop"] == {"userId": "me"}
    db.session.refresh(gmail_account)
    assert gmail_account.subscription_id is None and gmail_account.watch_expiry is None


# ── Realtime capability flag ─────────────────────────────────────────────────
@pytest.mark.parametrize("topic,audience,sa,expected,missing", [
    (TOPIC, "aud", "sa@p", True, []),
    (None, "aud", "sa@p", False, ["GMAIL_PUBSUB_TOPIC"]),
    (TOPIC, None, "sa@p", False, ["GMAIL_PUSH_AUDIENCE"]),
    (TOPIC, "aud", None, False, ["GMAIL_PUSH_SA_EMAIL"]),
    (None, None, None, False, ["GMAIL_PUBSUB_TOPIC", "GMAIL_PUSH_AUDIENCE", "GMAIL_PUSH_SA_EMAIL"]),
])
def test_realtime_needs_all_three_legs(topic, audience, sa, expected, missing):
    """A watch onto no topic, or a push we cannot authenticate, is not realtime — and the
    status screen must be able to name which leg is missing."""
    from app.config import BaseConfig

    cfg = BaseConfig()
    cfg.GMAIL_PUBSUB_TOPIC, cfg.GMAIL_PUSH_AUDIENCE, cfg.GMAIL_PUSH_SA_EMAIL = topic, audience, sa
    assert cfg.GMAIL_REALTIME is expected
    assert cfg.GMAIL_REALTIME_MISSING == missing


# ── What the admin actually sees ─────────────────────────────────────────────
def _status_rows(app, org):
    from app.web.context.admin import _live_system_status

    return {row["name"]: row for row in _live_system_status(org)}


def test_status_row_follows_the_orgs_own_google_app(app, gmail_account):
    """The Beheer overview is framed per org, but read the platform flag — so a customer
    on its own Google app saw Gmail red while their mailbox worked fine."""
    org = gmail_account.user.organization
    assert _status_rows(app, org)["Gmail API + Pub/Sub"]["ok"] is False

    _byo_google(gmail_account)
    assert _status_rows(app, org)["Gmail API + Pub/Sub"]["ok"] is True


def test_status_row_does_not_claim_a_watch_that_was_never_registered(app, gmail_account, monkeypatch):
    """It reported "Watch actief op N postvakken" for every connected Gmail mailbox,
    including the ones nothing had ever watched — which was all of them."""
    _byo_google(gmail_account)
    org = gmail_account.user.organization

    detail = str(_status_rows(app, org)["Gmail API + Pub/Sub"]["detail"])
    assert "Watch actief" not in detail
    assert "GMAIL_PUBSUB_TOPIC" in detail  # names the missing setting instead

    _realtime(app, monkeypatch)
    detail = str(_status_rows(app, org)["Gmail API + Pub/Sub"]["detail"])
    assert "0" in detail and "1" in detail  # 0 of 1 mailbox watched — honest, not "actief"

    gmail_account.subscription_id = TOPIC
    db.session.commit()
    assert "1 van 1" in str(_status_rows(app, org)["Gmail API + Pub/Sub"]["detail"])


def test_status_row_without_any_gmail_mailbox(app, gmail_account):
    """Configured but nothing connected yet is neither red nor a watch claim."""
    org = gmail_account.user.organization
    _byo_google(gmail_account)
    db.session.delete(gmail_account)
    db.session.commit()

    row = _status_rows(app, org)["Gmail API + Pub/Sub"]
    assert row["ok"] is True and "nog geen Gmail-postvak" in str(row["detail"])


def test_gmail_scheduler_pass_includes_gmail_mailboxes(app, gmail_account, google_sdk, monkeypatch):
    """renew_subscriptions only ever queried provider='outlook', so no Gmail mailbox was
    ever armed or re-armed — the reason realtime never happened."""
    from app.tasks.schedule import renew_subscriptions

    _byo_google(gmail_account)
    _realtime(app, monkeypatch)
    monkeypatch.setitem(app.config, "GRAPH_REALTIME", False)

    renew_subscriptions()

    db.session.refresh(gmail_account)
    assert gmail_account.subscription_id == TOPIC


def test_scheduler_leaves_gmail_alone_when_realtime_is_off(app, gmail_account, google_sdk, monkeypatch):
    from app.tasks.schedule import renew_subscriptions

    _byo_google(gmail_account)
    monkeypatch.setitem(app.config, "GMAIL_REALTIME", False)
    monkeypatch.setitem(app.config, "GRAPH_REALTIME", False)

    renew_subscriptions()

    assert "watch" not in google_sdk.calls and gmail_account.subscription_id is None

"""One-click Microsoft 365 connect + tenant admin-consent.

An admin connects their mailbox AND grants organisation-wide consent in one flow
(prompt=admin_consent on the normal authorization-code connect, reusing the registered
redirect URI — no extra Azure config). That clears the "Goedkeuring van beheerder
vereist" wall the admin-consent-only User.ReadBasic.All scope raises, so every other
user then connects without a consent prompt.
"""
import sys
import types

from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _login(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _graph_on(app, monkeypatch):
    # Pretend the shared platform app is configured (demo org has no BYO app).
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setitem(app.config, "MS_CLIENT_ID", "platform-cid")
    monkeypatch.setitem(app.config, "MS_CLIENT_SECRET", "platform-secret")


def _demo_user() -> User:
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _fake_msal(monkeypatch, *, token=None):
    """Inject a stand-in `msal` module (it's a prod-only dep, absent in CI) and capture
    what the connect flow hands it."""
    captured = {}

    class _App:
        def __init__(self, *a, **k):
            pass

        def get_authorization_request_url(self, scopes, redirect_uri=None, **kwargs):
            captured["kwargs"] = kwargs
            captured["redirect_uri"] = redirect_uri
            return "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?fake=1"

        def acquire_token_by_authorization_code(self, code, scopes=None, redirect_uri=None):
            captured["code"] = code
            return token if token is not None else {
                "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                "id_token_claims": {"preferred_username": "admin@acme.be"},
            }

    fake = types.ModuleType("msal")
    fake.ConfidentialClientApplication = _App
    monkeypatch.setitem(sys.modules, "msal", fake)
    return captured


# ── start: admin connect forces the org-wide consent dialog (prompt=consent) ──
def test_admin_connect_passes_consent_prompt(app, client, monkeypatch):
    captured = _fake_msal(monkeypatch)
    _graph_on(app, monkeypatch)
    _login(client)

    r = client.get("/auth/microsoft/admin-consent", follow_redirects=False)
    assert r.status_code == 302
    # 'consent' (valid on v2.0); 'admin_consent' is a legacy v1.0 value that v2.0 rejects
    # with AADSTS901001. An admin granting an admin-only scope is tenant-wide.
    assert captured["kwargs"].get("prompt") == "consent"
    # Reuses the SAME registered connect callback — no separate redirect URI to register.
    assert captured["redirect_uri"].endswith("/auth/microsoft/callback")


def test_normal_connect_has_no_admin_prompt(app, client, monkeypatch):
    captured = _fake_msal(monkeypatch)
    _graph_on(app, monkeypatch)
    _login(client)

    client.get("/auth/microsoft", follow_redirects=False)
    assert "prompt" not in captured["kwargs"]


def test_admin_connect_blocked_without_manage_providers(app, client, monkeypatch):
    captured = _fake_msal(monkeypatch)
    _graph_on(app, monkeypatch)
    _login(client)
    monkeypatch.setattr("app.models.user.User.has_perm", lambda self, perm: False)

    r = client.get("/auth/microsoft/admin-consent", follow_redirects=False)
    assert r.status_code == 302
    assert "login.microsoftonline.com" not in r.headers["Location"]  # bounced
    assert captured == {}  # never reached Microsoft


def test_admin_connect_unconfigured_bounces(app, client, monkeypatch):
    captured = _fake_msal(monkeypatch)
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", False)
    _login(client)
    client.get("/auth/microsoft/admin-consent", follow_redirects=False)
    assert captured == {}


# ── callback: an admin connect stamps organisation-wide consent ───────────────
def test_callback_stamps_consent_after_admin_connect(app, client, monkeypatch):
    _fake_msal(monkeypatch)
    monkeypatch.setattr("app.auth.oauth.state._kick_initial_import", lambda *a, **k: None)
    _graph_on(app, monkeypatch)
    _login(client)

    client.get("/auth/microsoft/admin-consent")  # sets the pending flag in session
    assert _demo_user().organization.graph_admin_consented_at is None

    r = client.get("/auth/microsoft/callback?code=abc123", follow_redirects=False)
    assert r.status_code == 302
    user = _demo_user()
    assert user.organization.graph_admin_consented_at is not None  # consent recorded
    assert any(a.provider == "outlook" for a in user.mail_accounts)  # mailbox connected


def test_normal_connect_callback_does_not_stamp_consent(app, client, monkeypatch):
    _fake_msal(monkeypatch)
    monkeypatch.setattr("app.auth.oauth.state._kick_initial_import", lambda *a, **k: None)
    _graph_on(app, monkeypatch)
    _login(client)

    client.get("/auth/microsoft")  # normal connect — no admin flag
    client.get("/auth/microsoft/callback?code=abc123", follow_redirects=False)
    assert _demo_user().organization.graph_admin_consented_at is None


# ── the first-connect import must NOT block the OAuth callback ─────────────────
def test_kick_initial_import_runs_off_thread_when_eager(app, monkeypatch):
    # In the no-worker (eager) deployment, importing 300 messages + drafts inline would
    # time the callback out (the reported 500). It must run in a background thread instead.
    import threading

    from app.auth.oauth import state

    started, release, done = threading.Event(), threading.Event(), {}

    def fake_import(account_id):
        started.set()
        release.wait(2)  # hold the "import" open so we can prove the caller didn't block
        done["id"] = account_id

    monkeypatch.setattr("app.tasks.sync.initial_import", fake_import)

    with app.app_context():
        state._kick_initial_import(42)
        assert started.wait(2)        # the import is running...
        assert "id" not in done       # ...yet _kick_initial_import already returned (non-blocking)
        release.set()
    assert started.is_set()


# ── backoffice E-mailkoppeling screen: real state + the button ────────────────
def test_backoffice_shows_connect_consent_button(app, client, monkeypatch):
    _graph_on(app, monkeypatch)
    _login(client)
    html = client.get("/admin/providers").get_data(as_text=True)
    assert "Koppelen &amp; goedkeuren voor organisatie" in html
    assert "Goedkeuring van beheerder vereist" in html  # the explainer for the not-yet state


def test_backoffice_shows_approved_state(app, client, monkeypatch):
    from app.models.base import utcnow

    _graph_on(app, monkeypatch)
    _login(client)
    org = _demo_user().organization
    org.graph_admin_consented_at = utcnow()
    db.session.commit()

    html = client.get("/admin/providers").get_data(as_text=True)
    assert "Goedgekeurd voor de hele organisatie" in html


# ── callback error surfacing: show the REAL reason, never a silent bounce ──────
def test_callback_surfaces_microsoft_error(app, client, monkeypatch):
    """Microsoft redirects back with ?error=... (e.g. admin approval required) and no
    code — the user must see the real reason, not a vague 'cancelled'."""
    _graph_on(app, monkeypatch)
    _login(client)
    _demo_user().onboarded = False  # so the onboarding page renders the flash
    db.session.commit()
    r = client.get(
        "/auth/microsoft/callback?error=access_denied"
        "&error_description=AADSTS65001+admin+approval+required",
        follow_redirects=True,
    )
    body = r.get_data(as_text=True)
    assert "geweigerd" in body.lower()
    assert "admin approval required" in body.lower()


# ── consent-only: goedkeuren ZONDER het postvak van de admin te koppelen ───────
def test_consent_only_stamps_consent_without_connecting_a_mailbox(app, client, monkeypatch):
    """?koppel=0 — de IT-admin keurt Otto goed voor de organisatie, maar zijn eigen
    postvak wordt NIET gekoppeld; de echte gebruiker koppelt daarna zijn eigen postvak."""
    _fake_msal(monkeypatch)
    _graph_on(app, monkeypatch)
    _login(client)
    before = {a.id for a in _demo_user().mail_accounts}

    client.get("/auth/microsoft/admin-consent?koppel=0")
    r = client.get("/auth/microsoft/callback?code=abc123", follow_redirects=False)

    assert r.status_code == 302 and "/app/integrations" in r.headers["Location"]
    user = _demo_user()
    assert user.organization.graph_admin_consented_at is not None   # consent vastgelegd
    assert {a.id for a in user.mail_accounts} == before             # géén postvak bij
    assert not any(a.email == "admin@acme.be" for a in user.mail_accounts)


def test_consent_only_flag_does_not_leak_into_a_later_normal_connect(app, client, monkeypatch):
    """Wie eerst alleen goedkeurde en daarna gewoon koppelt, krijgt gewoon een postvak —
    de sessievlag mag niet blijven plakken."""
    _fake_msal(monkeypatch)
    monkeypatch.setattr("app.auth.oauth.state._kick_initial_import", lambda *a, **k: None)
    _graph_on(app, monkeypatch)
    _login(client)

    client.get("/auth/microsoft/admin-consent?koppel=0")
    client.get("/auth/microsoft/callback?code=abc123", follow_redirects=False)
    client.get("/auth/microsoft")  # daarna een gewone koppeling
    client.get("/auth/microsoft/callback?code=abc456", follow_redirects=False)

    assert any(a.email == "admin@acme.be" for a in _demo_user().mail_accounts)


def test_both_screens_offer_the_consent_only_link(app, client, monkeypatch):
    """De 'Alleen goedkeuren'-optie staat op Integraties én op Beheer → E-mailkoppeling."""
    _graph_on(app, monkeypatch)
    _login(client)
    for pad in ("/app/integrations", "/admin/providers"):
        page = client.get(pad).get_data(as_text=True)
        assert "koppel=0" in page, pad
        assert "Alleen goedkeuren" in page, pad

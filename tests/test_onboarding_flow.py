"""Onboarding without an instance-wide AI model or mailbox app: own AI key + own mailbox
app reachable + coherent, and the one-time post-onboarding intro ("Rondleiding")."""
from __future__ import annotations

from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _connect_owner(app, email="con@connectbv.be", onboarded=False):
    from app.auth.service import create_company

    with app.app_context():
        create_company("Connect BV", "Con Owner", email, "geheim123")
        u = db.session.query(User).filter_by(email=email).first()
        u.email_verified = True
        u.onboarded = onboarded  # set before login so the request sees the right state
        db.session.commit()
    return email


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})


def test_onboarding_shows_ai_key_step_without_instance_model(client, app):
    email = _connect_owner(app)
    _login(client, email)
    body = client.get("/auth/onboarding").get_data(as_text=True)
    assert "Je AI-sleutel" in body            # the own-key step is shown (no instance model)
    assert 'name="provider"' in body          # provider (welk bedrijf) picker
    assert 'name="model"' in body             # model field
    # The per-provider "how to get your key" guide renders with real console links.
    assert 'id="byo-key-guide"' in body
    assert "console.anthropic.com/settings/keys" in body
    assert "aistudio.google.com/apikey" in body


def test_onboarding_leads_with_consent_when_platform_app_configured(client, app):
    """With the shared platform app configured, onboarding sells the one-click consent
    connect (no per-customer app), and demotes the own-app route to 'Geavanceerd'."""
    app.config["GRAPH_CONFIGURED"] = True
    email = _connect_owner(app)
    _login(client, email)
    body = client.get("/auth/onboarding").get_data(as_text=True)
    assert "toestemming" in body.lower()      # consent-first messaging
    assert "Geavanceerd" in body              # own-app is the advanced option
    assert "/app/connector" in body           # still reachable for those who want it


def test_onboarding_offers_admin_consent_for_owner(client, app):
    """With the shared app configured, the owner gets a one-click 'approve for the whole
    organization' link (for tenants that block user consent)."""
    app.config["GRAPH_CONFIGURED"] = True
    email = _connect_owner(app)
    _login(client, email)
    body = client.get("/auth/onboarding").get_data(as_text=True)
    assert "/auth/microsoft/admin-consent" in body
    assert "hele organisatie" in body.lower()


def test_connect_button_routes_to_own_app_instead_of_dead_end(client, app):
    """No shared platform app + no own app yet → 'Verbinden' takes the owner to the
    own-mailbox-app screen (not a silent dead-end)."""
    app.config["GRAPH_CONFIGURED"] = False
    email = _connect_owner(app)
    _login(client, email)
    r = client.post("/auth/onboarding/connect", data={"provider": "outlook"}, follow_redirects=False)
    assert r.status_code == 302 and "/app/connector" in r.headers["Location"]


def test_setup_screens_reachable_during_onboarding(client, app):
    """The own-app and AI screens must be reachable while NOT yet onboarded, else the
    Connect customer can never finish setup."""
    email = _connect_owner(app)
    _login(client, email)
    assert client.get("/app/connector").status_code == 200          # own mailbox app
    assert client.get("/admin/ai").status_code == 200               # AI key screen
    # A non-setup app page still funnels back to onboarding.
    assert client.get("/app/inbox").status_code == 302
    assert "onboarding" in client.get("/app/inbox").headers["Location"]


def test_saving_ai_key_from_onboarding_returns_to_onboarding(client, app):
    email = _connect_owner(app)
    _login(client, email)
    r = client.post("/admin/ai/byo", data={
        "provider": "anthropic", "model": "claude-sonnet-4-6",
        "api_key": "sk-test-123", "next": "onboarding",
    }, follow_redirects=False)
    assert r.status_code == 302 and "onboarding" in r.headers["Location"]
    with app.app_context():
        org = db.session.query(User).filter_by(email=email).first().organization
        assert org.ai.byo_provider == "anthropic" and org.ai.byo_api_key_enc


def test_own_microsoft_app_then_connect_starts_oauth(client, app):
    """With the AI key + own Microsoft app set, 'Verbinden' starts the real OAuth."""
    app.config["GRAPH_CONFIGURED"] = False
    email = _connect_owner(app)
    _login(client, email)
    client.post("/admin/ai/byo", data={"provider": "anthropic", "model": "m",
                                        "api_key": "sk-x", "next": "onboarding"})
    r = client.post("/app/connector", data={
        "graph_app_id": "11111111-1111-1111-1111-111111111111",
        "graph_tenant_id": "organizations",
        "graph_client_secret": "byo-secret",
    }, follow_redirects=False)
    assert r.status_code == 302 and "onboarding" in r.headers["Location"]  # returns to onboarding
    r2 = client.post("/auth/onboarding/connect", data={"provider": "outlook"}, follow_redirects=False)
    assert r2.status_code == 302 and "/auth/microsoft" in r2.headers["Location"]


def test_rondleiding_shown_once_and_marks_seen(client, app):
    email = _connect_owner(app, onboarded=True)
    _login(client, email)
    with app.app_context():
        assert db.session.query(User).filter_by(email=email).first().intro_seen is False
    # Reachable even for a keyless Connect org (the intro itself points to the AI key).
    assert client.get("/app/rondleiding").status_code == 200
    with app.app_context():
        assert db.session.query(User).filter_by(email=email).first().intro_seen is True


def test_sidebar_has_rondleiding_link(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    body = client.get("/app/inbox").get_data(as_text=True)
    assert "Rondleiding" in body
    assert "/app/rondleiding" in body


# ── The connect promise must hold per provider ───────────────────────────────
def _onboarding_body(client, app, *, graph=False, gmail=False):
    app.config["GRAPH_CONFIGURED"], app.config["GMAIL_CONFIGURED"] = graph, gmail
    email = _connect_owner(app, email=f"p{int(graph)}{int(gmail)}@promisebv.be")
    _login(client, email)
    return client.get("/auth/onboarding").get_data(as_text=True)


def test_unconfigured_provider_does_not_offer_a_connect_that_bounces(client, app):
    """Gmail without a platform app sends you to the own-app screen, so the tile must not
    say 'Verbinden'. It used to — under a banner promising 'geen configuratie'."""
    body = _onboarding_body(client, app, graph=True, gmail=False)

    assert "nog niet beschikbaar op deze omgeving" in body
    assert "Eigen app instellen" in body
    # And the reassurance names only the provider it is actually true for.
    assert "vraagt Microsoft je eenmalig toestemming" in body
    assert "vraagt Microsoft of Google" not in body
    assert "Google is op deze omgeving nog niet beschikbaar" in body


def test_both_platform_apps_promise_one_click_for_both(client, app):
    body = _onboarding_body(client, app, graph=True, gmail=True)

    assert "vraagt Microsoft of Google je eenmalig toestemming" in body
    assert "nog niet beschikbaar op deze omgeving" not in body
    assert "klaar om te koppelen" in body


def test_no_platform_app_promises_nothing(client, app):
    """Neither configured: never claim one-click, and point at the own-app route."""
    body = _onboarding_body(client, app, graph=False, gmail=False)

    assert "Eén klik, geen configuratie" not in body
    assert "nog niet beschikbaar op deze omgeving" in body
    assert "/app/connector" in body


def test_gmail_connect_without_the_sdk_is_not_a_500(client, app, monkeypatch):
    """Credentials set but the Google libraries missing is a deployment mistake. The
    connect button must say so, not crash: the OAuth flow cannot even be built."""
    import sys

    app.config["GMAIL_CONFIGURED"] = True
    app.config["GOOGLE_CLIENT_ID"], app.config["GOOGLE_CLIENT_SECRET"] = "id", "secret"
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)  # -> ImportError
    email = _connect_owner(app, email="nosdk@promisebv.be")
    _login(client, email)

    r = client.post("/auth/onboarding/connect", data={"provider": "gmail"}, follow_redirects=True)
    assert r.status_code == 200
    assert "nog niet volledig geïnstalleerd" in r.get_data(as_text=True)

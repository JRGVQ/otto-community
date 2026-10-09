"""Registration (with e-mail verification), login gating, logout."""
from app.email import make_verify_token
from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _register(client, company="Nieuw Bedrijf", email="nieuw@nieuwbedrijf.be"):
    return client.post("/auth/register", data={
        "company": company, "name": "Nieuwe Gebruiker", "email": email,
        "password": "geheim123", "confirm": "geheim123",
    }, follow_redirects=False)


def test_register_creates_unverified_company(client, app):
    r = _register(client)
    assert r.status_code == 200  # "check your mail" page, not logged in yet
    assert "Check je mailbox" in r.get_data(as_text=True)
    user = db.session.query(User).filter_by(email="nieuw@nieuwbedrijf.be").first()
    assert user is not None and user.email_verified is False and user.onboarded is False
    assert user.organization.name == "Nieuw Bedrijf" and user.role_name == "Eigenaar"


def test_unverified_user_cannot_login(client, app):
    _register(client, "Bedrijf X", "x@bedrijfx.be")
    r = client.post("/auth/login", data={"email": "x@bedrijfx.be", "password": "geheim123"})
    assert "Bevestig eerst je e-mail" in r.get_data(as_text=True)


def test_verify_link_activates_and_logs_in(client, app):
    _register(client, "Bedrijf Y", "y@bedrijfy.be")
    token = make_verify_token("y@bedrijfy.be")
    r = client.get(f"/auth/verify/{token}", follow_redirects=False)
    assert r.status_code == 302 and "onboarding" in r.headers["Location"]
    assert db.session.query(User).filter_by(email="y@bedrijfy.be").first().email_verified is True


def test_invalid_verify_token(client, app):
    assert "Ongeldige link" in client.get("/auth/verify/not-a-real-token").get_data(as_text=True)


def test_onboarding_finish_enters_empty_app(client, app):
    """Setup checklist: a new owner can enter the (empty) app and configure from inside."""
    _register(client, "Setup BV", "s@setupbv.be")
    client.get(f"/auth/verify/{make_verify_token('s@setupbv.be')}")  # verify => logged in, not onboarded
    assert client.get("/app/inbox").status_code == 302  # gated to the setup screen

    # First finish → the one-time post-onboarding intro ("Rondleiding").
    r = client.post("/auth/onboarding/finish", follow_redirects=False)
    assert r.status_code == 302 and "rondleiding" in r.headers["Location"]
    assert db.session.query(User).filter_by(email="s@setupbv.be").first().onboarded is True
    assert client.get("/app/rondleiding").status_code == 200  # marks intro_seen
    body = client.get("/app/inbox").get_data(as_text=True)
    assert "inbox is nog leeg" in body.lower()  # empty-state, no demo data


def test_onboarding_connect_uses_own_microsoft_app(client, app):
    """A company with its own (BYO) Microsoft app connects from onboarding even when
    the shared platform app isn't configured — consent stays in the customer's tenant."""
    from app.auth.service import create_company
    from app.security.crypto import encrypt

    app.config["GRAPH_CONFIGURED"] = False  # no shared platform app
    create_company("BYO BV", "Byo Owner", "byo@byobv.be", "geheim123")
    user = db.session.query(User).filter_by(email="byo@byobv.be").first()
    user.email_verified = True
    user.organization.graph_app_id = "11111111-1111-1111-1111-111111111111"
    user.organization.graph_client_secret_enc = encrypt("byo-secret")
    db.session.commit()

    client.post("/auth/login", data={"email": "byo@byobv.be", "password": "geheim123"})
    r = client.post("/auth/onboarding/connect", data={"provider": "outlook"}, follow_redirects=False)
    assert r.status_code == 302 and "/auth/microsoft" in r.headers["Location"]


def test_login_correct_then_logout(client, app):
    # The seeded demo user is pre-verified.
    r = client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD}, follow_redirects=False)
    assert r.status_code == 302
    assert client.get("/app/inbox").status_code == 200
    client.get("/auth/logout")
    assert client.get("/app/inbox").status_code == 302


def test_production_register_never_leaks_the_link(client, app):
    """Production (SHOW_VERIFY_LINK off, no SMTP): the raw verify link is never shown."""
    app.config["SHOW_VERIFY_LINK"] = False
    body = _register(client, "Prod BV", "p@prodbv.be").get_data(as_text=True)
    assert "Check je mailbox" in body
    assert "/auth/verify/" not in body
    assert "Dev-modus" not in body and "Test-modus" not in body


def test_test_environment_surfaces_the_link(client, app):
    """Test environment (SHOW_VERIFY_LINK on): the link is surfaced for convenience."""
    app.config["SHOW_VERIFY_LINK"] = True
    body = _register(client, "Test BV", "t@testbv.be").get_data(as_text=True)
    assert "/auth/verify/" in body and "Test-modus" in body


def test_verify_user_cli_activates(app):
    """Admin escape hatch: activate an account without SMTP."""
    from app.auth.service import create_company

    create_company("CLI BV", "Cli Owner", "c@clibv.be", "geheim123")
    assert db.session.query(User).filter_by(email="c@clibv.be").first().email_verified is False
    out = app.test_cli_runner().invoke(args=["verify-user", "c@clibv.be"]).output
    assert "verified" in out
    db.session.expire_all()
    assert db.session.query(User).filter_by(email="c@clibv.be").first().email_verified is True


def test_forgot_password_surfaces_reset_link_in_test_env(client, app):
    """Test environment (SHOW_VERIFY_LINK on): the reset link is shown for convenience."""
    app.config["SHOW_VERIFY_LINK"] = True
    body = client.post("/auth/forgot", data={"email": "demo@example.com"}).get_data(as_text=True)
    assert "Check je mailbox" in body
    assert "/auth/reset/" in body and "Test-modus" in body


def test_forgot_password_is_neutral_for_unknown_email(client, app):
    """No account enumeration: an unknown address gets the same page and no link."""
    app.config["SHOW_VERIFY_LINK"] = True
    body = client.post("/auth/forgot", data={"email": "nobody@nowhere.be"}).get_data(as_text=True)
    assert "Check je mailbox" in body
    assert "/auth/reset/" not in body


def test_production_forgot_never_leaks_the_link(client, app):
    """Production (SHOW_VERIFY_LINK off, no SMTP): the raw reset link is never shown."""
    app.config["SHOW_VERIFY_LINK"] = False
    body = client.post("/auth/forgot", data={"email": "demo@example.com"}).get_data(as_text=True)
    assert "Check je mailbox" in body
    assert "/auth/reset/" not in body and "Test-modus" not in body


def test_reset_link_sets_new_password_then_logs_in(client, app):
    from app.email import make_reset_token

    user = db.session.query(User).filter_by(email="demo@example.com").first()
    token = make_reset_token(user)
    assert "Kies een nieuw wachtwoord" in client.get(f"/auth/reset/{token}").get_data(as_text=True)

    r = client.post(f"/auth/reset/{token}",
                    data={"password": "nieuwgeheim1", "confirm": "nieuwgeheim1"}, follow_redirects=False)
    assert r.status_code == 302 and "login" in r.headers["Location"]

    db.session.expire_all()
    ok = client.post("/auth/login", data={"email": "demo@example.com", "password": "nieuwgeheim1"},
                     follow_redirects=False)
    assert ok.status_code == 302  # the new password works


def test_reset_token_is_single_use(client, app):
    """Once the password changes, the same link self-invalidates."""
    from app.email import make_reset_token

    user = db.session.query(User).filter_by(email="demo@example.com").first()
    token = make_reset_token(user)
    client.post(f"/auth/reset/{token}", data={"password": "eerstegeheim1", "confirm": "eerstegeheim1"})
    assert "Ongeldige link" in client.get(f"/auth/reset/{token}").get_data(as_text=True)


def test_reset_password_mismatch_is_rejected(client, app):
    from app.email import make_reset_token

    user = db.session.query(User).filter_by(email="demo@example.com").first()
    token = make_reset_token(user)
    body = client.post(f"/auth/reset/{token}",
                       data={"password": "geheim123", "confirm": "andersgeheim"}).get_data(as_text=True)
    assert "komen niet overeen" in body


def test_invalid_reset_token(client, app):
    assert "Ongeldige link" in client.get("/auth/reset/not-a-real-token").get_data(as_text=True)


def test_mailer_routes_to_graph_when_selected(app, monkeypatch):
    """MAIL_PROVIDER=graph sends via Microsoft Graph (free M365), not SMTP."""
    import app.email as email_mod

    app.config.update(MAIL_PROVIDER="graph", GRAPH_MAIL_CLIENT_ID="id", GRAPH_MAIL_CLIENT_SECRET="sec",
                      GRAPH_MAIL_TENANT_ID="tid", GRAPH_MAIL_SENDER="otto@example.com", SMTP_HOST=None)
    seen = {}
    monkeypatch.setattr(email_mod, "_send_via_graph", lambda *a, **k: seen.setdefault("graph", True) or True)
    assert email_mod.send_email("u@x.be", "Subj", "<b>h</b>", "h") is True and seen.get("graph")


def test_mailer_returns_false_without_backend(app):
    """No SMTP and no Graph configured → no crash, just 'not sent'."""
    import app.email as email_mod

    app.config.update(MAIL_PROVIDER="smtp", SMTP_HOST=None)
    assert email_mod.send_email("u@x.be", "S", "<b>h</b>", "h") is False


# ── Registration error handling (never dead-end on a 500) ─────────────────────
def test_register_survives_a_very_long_company_name(client, app):
    """A company name can be twice the slug column's width (the form allows 128 chars).
    The org slug is String(64), so the base must be trimmed to leave room for a suffix —
    otherwise the INSERT overflows the column and the visitor gets a raw 500."""
    from app.tenancy import SLUG_MAX_LEN, slugify

    long_name = "Verhaeghe Vandenberghe Advocatenassociatie Internationaal Gent BV"  # >64, ≤128
    assert len(long_name) > SLUG_MAX_LEN  # would overflow slug(64) untrimmed
    assert len(slugify(long_name)) <= SLUG_MAX_LEN

    r = _register(client, long_name, "lang@voorbeeldbedrijf.be")
    assert r.status_code == 200  # "check your mail", not a 500
    user = db.session.query(User).filter_by(email="lang@voorbeeldbedrijf.be").first()
    assert user is not None
    assert len(user.organization.slug) <= SLUG_MAX_LEN


def test_register_recovers_gracefully_when_provisioning_fails(client, app, monkeypatch):
    """If org/owner creation blows up part-way, the visitor gets the form back with a
    clear message — never a dead-end 500 — and nothing is left half-committed, so the
    address stays free to try again."""
    import app.auth.routes as routes

    def boom(*a, **k):
        # Mimic a provisioning error that poisons the request's db session.
        db.session.add(User(email="", name="", organization_id=None))
        db.session.flush()  # raises: email is NOT NULL/unique
        raise AssertionError("unreachable")

    monkeypatch.setattr(routes, "create_company", boom)

    r = client.post("/auth/register", data={
        "company": "Valbedrijf", "name": "Val Gebruiker", "email": "val@valbedrijf.be",
        "password": "geheim123", "confirm": "geheim123",
    }, follow_redirects=False)

    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "Er ging iets mis bij het aanmaken" in body      # friendly message on the form
    assert '/auth/register' in body or 'name="email"' in body  # still the registration form
    # The session recovered (rolled back): a normal query works and no account was created.
    assert db.session.query(User).filter_by(email="val@valbedrijf.be").first() is None


def test_register_page_is_reusable_after_a_failed_attempt(client, app, monkeypatch):
    """After a failed attempt the user isn't stuck: the very next registration succeeds."""
    import app.auth.routes as routes

    real = routes.create_company
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            db.session.execute(db.text("SELECT * FROM does_not_exist"))  # poison + raise
        return real(*a, **k)

    monkeypatch.setattr(routes, "create_company", flaky)

    first = client.post("/auth/register", data={
        "company": "Herkans BV", "name": "Twee Keer", "email": "twee@herkansbv.be",
        "password": "geheim123", "confirm": "geheim123",
    })
    assert first.status_code == 500

    second = _register(client, "Herkans BV", "twee@herkansbv.be")
    assert second.status_code == 200
    assert db.session.query(User).filter_by(email="twee@herkansbv.be").first() is not None

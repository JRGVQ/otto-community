"""Login gate, language cookie, demo seeding, error capture, healthz."""

from app.extensions import db
from app.models import ErrorLog, Organization


# ── Routing ─────────────────────────────────────────────────────────────────
def test_app_host_requires_login(app):
    r = app.test_client().get("/app/inbox", headers={"Host": "otto.example.com"})
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_language_cookie_host_only_on_localhost(app):
    """On localhost/dev the cookie stays host-only — no parent Domain= (nothing to share)."""
    sc = app.test_client().get("/lang/fr?next=/", base_url="http://localhost").headers.get("Set-Cookie", "")
    assert "lang=fr" in sc and "Domain=" not in sc


def test_topbar_user_links_to_settings(auth_client):
    """Clicking the user (name/avatar) opens account settings."""
    body = auth_client.get("/app/inbox").get_data(as_text=True)
    assert 'class="user-link"' in body and "/app/settings" in body


def test_integrations_real_connect_and_no_fake_toggle(auth_client, app):
    """Integraties: real OAuth for the mailbox, honest 'Binnenkort' for the rest —
    and never the old fake connect-toggle."""
    app.config["GRAPH_CONFIGURED"] = True
    body = auth_client.get("/app/integrations").get_data(as_text=True)
    assert "/auth/microsoft" in body                       # real Graph OAuth
    assert "Binnenkort" in body                             # other integrations are honest
    assert 'data-action="toggle-integration"' not in body  # no fake connect

    app.config["GRAPH_CONFIGURED"] = False
    app.config["GMAIL_CONFIGURED"] = False
    body = auth_client.get("/app/integrations").get_data(as_text=True)
    assert "nog niet geconfigureerd" in body               # honest when no OAuth is wired


def test_integration_toggle_never_fakes_connected(auth_client):
    """The legacy toggle endpoint must never report a fake 'connected' state."""
    data = auth_client.post("/api/integrations/slack/toggle").get_json()
    assert data["connected"] is False


# ── Demo seed ────────────────────────────────────────────────────────────────
def test_seed_never_resurrects_demo_on_a_real_environment(app):
    """SEED_DEMO=1 runs the seed on every container boot. Once the operator has wiped
    the demo org and created their own company, a redeploy must NOT bring demo back."""
    from app.seed import seed_all
    from app.tenancy import delete_org_deep, provision_organization

    demo = db.session.query(Organization).filter_by(name="Acme NV").first()
    delete_org_deep(demo)                                   # like `flask wipe-demo`
    provision_organization("Echte BV", domain="echte.be", demo=False)  # `flask create-owner`
    db.session.commit()

    result = seed_all()                                     # the boot-time reseed
    assert result["status"] == "exists"                    # no-op, not a fresh seed
    assert db.session.query(Organization).filter_by(name="Acme NV").first() is None


# ── Health & error capture ───────────────────────────────────────────────────
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json()["db"] == "ok"


def test_error_capture_and_debug_console(auth_client, app):
    app.config["PROPAGATE_EXCEPTIONS"] = False  # let the error handler run in-test
    before = db.session.query(ErrorLog).count()

    r = auth_client.get("/owner/debug/test-error")
    assert r.status_code == 500
    assert db.session.query(ErrorLog).count() == before + 1

    logged = db.session.query(ErrorLog).order_by(ErrorLog.id.desc()).first()
    assert logged.exception_type == "RuntimeError" and logged.status_code == 500
    assert "RuntimeError" in auth_client.get("/owner/debug").get_data(as_text=True)


def test_owner_health_and_debug_render(auth_client):
    assert auth_client.get("/owner/health").status_code == 200
    assert auth_client.get("/owner/debug").status_code == 200


def test_unique_slug_still_counts_up_for_real_companies(app):
    from app.tenancy import provision_organization, unique_slug

    assert unique_slug("Acme BV") == "acme-bv"
    provision_organization("Acme BV", domain="acme.be")
    db.session.commit()
    assert unique_slug("Acme BV") == "acme-bv-2"
    provision_organization("Acme BV", domain="acme.be")
    db.session.commit()
    assert unique_slug("Acme BV") == "acme-bv-3"



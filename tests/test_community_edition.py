"""Self-hosted edition: first-run setup, closed registration, no licensing, no website."""
from __future__ import annotations

from app.extensions import db
from app.models import License, Organization, User


def _empty_db(app):
    """Drop the seeded demo data so the instance looks freshly installed."""
    db.drop_all()
    db.create_all()


# ── First run ────────────────────────────────────────────────────────────────
def test_root_goes_to_setup_on_an_empty_install(app, client):
    _empty_db(app)
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/setup")
    r = client.get("/auth/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/setup")


def test_setup_creates_the_first_org_and_administrator(app, client):
    _empty_db(app)
    r = client.post("/auth/setup", data={
        "company": "Acme Corp", "name": "Sam Admin", "email": "sam@acme-corp.be",
        "password": "Sterk-Wachtwoord-1", "confirm": "Sterk-Wachtwoord-1",
    })
    assert r.status_code == 302 and "/auth/onboarding" in r.headers["Location"]
    user = db.session.query(User).filter_by(email="sam@acme-corp.be").one()
    assert user.is_platform_owner and user.email_verified
    assert user.organization.name == "Acme Corp"
    assert user.organization.license.is_active


def test_setup_is_gone_once_an_account_exists(app, client):
    r = client.get("/auth/setup")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")
    r = client.post("/auth/setup", data={
        "company": "Intruder", "name": "Eve", "email": "eve@evil.example",
        "password": "x" * 12, "confirm": "x" * 12,
    })
    assert r.status_code == 302
    assert db.session.query(User).filter_by(email="eve@evil.example").first() is None


# ── Registration ─────────────────────────────────────────────────────────────
def test_registration_is_closed_by_default(app, client):
    app.config["REGISTRATION_OPEN"] = False
    assert client.get("/auth/register").status_code == 403
    r = client.post("/auth/register", data={
        "name": "Nina", "email": "nina@new.be", "password": "geheim123", "confirm": "geheim123",
    })
    assert r.status_code == 403
    assert db.session.query(User).filter_by(email="nina@new.be").first() is None
    assert b"/auth/register" not in client.get("/auth/login").data  # no sign-up link


def test_open_registration_creates_an_active_org(app, client):
    app.config["REGISTRATION_OPEN"] = True
    r = client.post("/auth/register", data={
        "company": "Nieuw BV", "name": "Nina", "email": "nina@new.be",
        "password": "geheim123", "confirm": "geheim123",
    })
    assert r.status_code == 200
    user = db.session.query(User).filter_by(email="nina@new.be").one()
    assert user.organization.name == "Nieuw BV"
    assert user.organization.license.is_active
    assert not user.is_platform_owner


def test_registration_without_company_uses_the_persons_name(app, client):
    client.post("/auth/register", data={
        "name": "Nina Solo", "email": "nina@solo.be", "password": "geheim123", "confirm": "geheim123",
    })
    user = db.session.query(User).filter_by(email="nina@solo.be").one()
    assert user.organization.name == "Nina Solo"


# ── Access: active or suspended, nothing else ───────────────────────────────
def test_new_orgs_are_active_without_expiry(app):
    lic = License.new_active()
    assert lic.status == "active" and lic.is_active and not lic.is_blocked
    assert not hasattr(lic, "expires_on")


def test_suspended_org_is_blocked_and_reactivation_restores_access(app, client):
    from app.auth.service import create_company

    user = create_company("Schors BV", "Stan", "stan@schors.be", "geheim123")
    user.email_verified = user.onboarded = True
    user.organization.license.status = "suspended"
    db.session.commit()
    client.post("/auth/login", data={"email": "stan@schors.be", "password": "geheim123"})
    r = client.get("/app/inbox")
    assert r.status_code == 302 and "/licentie" in r.headers["Location"]
    assert "uitgeschakeld" in client.get("/licentie").get_data(as_text=True)

    org = db.session.get(Organization, user.organization_id)
    org.license.status = "active"
    db.session.commit()
    assert client.get("/app/inbox").status_code == 200


def test_owner_can_suspend_and_activate(app, auth_client):
    from app.auth.service import create_company

    other = create_company("Klant BV", "Kim", "kim@klant.be", "geheim123")
    org_id = other.organization_id
    auth_client.post(f"/owner/companies/{org_id}/status", data={"action": "suspend"})
    assert db.session.get(Organization, org_id).license.status == "suspended"
    auth_client.post(f"/owner/companies/{org_id}/status", data={"action": "activate"})
    assert db.session.get(Organization, org_id).license.status == "active"


def test_every_feature_is_available_without_a_plan(app, auth_client):
    for path in ("/app/teams", "/app/crm", "/admin/signatures"):
        r = auth_client.get(path)
        assert r.status_code == 200, path


# ── No public website ────────────────────────────────────────────────────────
def test_root_sends_visitors_to_login(app, client):
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")


def test_root_sends_signed_in_users_to_the_inbox(app, auth_client):
    r = auth_client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/inbox")


def test_robots_disallows_everything(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /" in r.get_data(as_text=True)


def test_marketing_pages_are_gone(client):
    for path in ("/functies", "/privacy", "/blog", "/wachtlijst", "/demo", "/sitemap.xml"):
        assert client.get(path).status_code == 404, path


def test_no_third_party_hosts_in_the_csp(client):
    policy = client.get("/auth/login").headers["Content-Security-Policy"]
    for host in ("googleapis", "gstatic", "googletagmanager", "google-analytics"):
        assert host not in policy


def test_admin_notice_goes_to_flagged_administrators(app, monkeypatch):
    from app import email as email_mod

    sent = []
    monkeypatch.setattr(email_mod, "send_email", lambda to, *a, **k: sent.append(to) or True)
    app.config["PLATFORM_OWNER_EMAIL"] = None
    admin = db.session.query(User).filter_by(email="demo@example.com").one()
    admin.is_platform_owner = True
    db.session.commit()
    assert email_mod.notify_platform_owner("x", "<p>y</p>") is True
    assert sent == ["demo@example.com"]


def test_addin_id_is_unique_per_installation(app):
    from app.addin.routes import addin_guid

    with app.test_request_context():
        app.config["ADDIN_ID"] = None
        first = addin_guid()
        app.config["SECRET_KEY"] = "another-installation"
        assert addin_guid() != first
        app.config["ADDIN_ID"] = "11111111-2222-3333-4444-555555555555"
        assert addin_guid() == "11111111-2222-3333-4444-555555555555"

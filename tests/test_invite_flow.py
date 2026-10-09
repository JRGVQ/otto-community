"""Invite-a-teammate flow: e-mail-only invite → accept page → activate + login."""
from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _login_admin(client):
    # Sam (demo@example.com) is a Beheerder → holds manageUsers.
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def test_invite_with_only_email_creates_pending_user(client, app):
    app.config["SHOW_VERIFY_LINK"] = True
    _login_admin(client)
    r = client.post("/api/admin/users/invite", json={"email": "nieuw@acme.be"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and "/auth/invite/" in body["link"]  # test-mode link surfaced

    user = db.session.query(User).filter_by(email="nieuw@acme.be").first()
    assert user is not None
    assert user.status == "Uitgenodigd"
    assert user.role_name == "Gebruiker"  # default role
    assert user.name == "Nieuw"  # derived from the e-mail prefix as a placeholder


def test_invite_rejects_bad_email_and_duplicates(client, app):
    _login_admin(client)
    assert client.post("/api/admin/users/invite", json={"email": "geenmail"}).status_code == 400
    # demo@example.com already exists
    dup = client.post("/api/admin/users/invite", json={"email": "demo@example.com"})
    assert dup.status_code == 400 and "bestaat al" in dup.get_json()["error"]


def test_accept_invite_activates_and_logs_in(client, app):
    from app.email import make_invite_token

    app.config["SHOW_VERIFY_LINK"] = True
    _login_admin(client)
    client.post("/api/admin/users/invite", json={"email": "jan@acme.be"})
    client.get("/auth/logout")

    token = make_invite_token("jan@acme.be")
    page = client.get(f"/auth/invite/{token}")
    assert page.status_code == 200 and "jan@acme.be" in page.get_data(as_text=True)

    r = client.post(f"/auth/invite/{token}", data={
        "name": "Jan Janssens", "password": "geheim123", "confirm": "geheim123",
    }, follow_redirects=False)
    assert r.status_code == 302 and "onboarding" in r.headers["Location"]

    db.session.expire_all()
    user = db.session.query(User).filter_by(email="jan@acme.be").first()
    assert user.status == "Actief" and user.email_verified is True
    assert user.name == "Jan Janssens"
    # The chosen password now works.
    client.get("/auth/logout")
    ok = client.post("/auth/login", data={"email": "jan@acme.be", "password": "geheim123"},
                     follow_redirects=False)
    assert ok.status_code == 302


def test_accept_invite_rejects_stale_or_active(client, app):
    from app.email import make_invite_token

    # No such invited user → bounced to login with an info flash.
    token = make_invite_token("ghost@acme.be")
    r = client.get(f"/auth/invite/{token}", follow_redirects=False)
    assert r.status_code == 302 and "login" in r.headers["Location"]

    # An already-active user's token is not accepted either.
    active = make_invite_token("demo@example.com")
    r2 = client.get(f"/auth/invite/{active}", follow_redirects=False)
    assert r2.status_code == 302 and "login" in r2.headers["Location"]


def test_revoke_invite_frees_the_seat(client, app):
    _login_admin(client)
    client.post("/api/admin/users/invite", json={"email": "weg@acme.be"})
    user = db.session.query(User).filter_by(email="weg@acme.be").first()
    assert user is not None

    r = client.post(f"/api/admin/users/{user.id}/invite/revoke")
    assert r.status_code == 200 and r.get_json()["ok"]
    db.session.expire_all()
    assert db.session.query(User).filter_by(email="weg@acme.be").first() is None


def test_invite_requires_permission(client, app):
    """A read-only member cannot invite (no manageUsers)."""
    # Provision a read-only user in the demo org and log in as them.
    from app.models import Organization, Role
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Alleen-lezen").first()
    u = User(email="ro@acme.be", name="Read Only", organization_id=org.id,
             role_id=role.id, status="Actief", email_verified=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()

    client.post("/auth/login", data={"email": "ro@acme.be", "password": "geheim123"})
    r = client.post("/api/admin/users/invite", json={"email": "x@acme.be"})
    assert r.status_code == 403

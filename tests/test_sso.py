"""Per-company login SSO (OIDC): domain resolution, JIT, config."""
from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _enable_sso(org, domains, jit=True):
    from app.security.crypto import encrypt

    sso = org.sso
    sso.oidc_issuer = "https://idp.example/tenant/v2.0"
    sso.oidc_client_id = "cid"
    sso.oidc_client_secret_enc = encrypt("sec")
    sso.allowed_domains = domains
    sso.enabled = True
    sso.jit = jit
    db.session.commit()


def test_sso_domain_resolution_and_jit(app):
    from app.auth.oauth.oidc import _find_or_provision, sso_org_for_email
    from app.auth.service import create_company

    org = create_company("SSO BV", "Owner", "owner@ssobv.be", "geheim123").organization
    _enable_sso(org, ["ssobv.be"])

    assert sso_org_for_email("nieuw@ssobv.be").id == org.id      # matched by domain
    assert sso_org_for_email("x@elders.be") is None              # no SSO for this domain

    user = _find_or_provision(org, "employee@ssobv.be", "Emp Loyee")
    assert user is not None
    assert user.organization_id == org.id and user.email_verified is True and user.onboarded is True

    create_company("Andere BV", "O", "o@andere.be", "geheim123")  # user exists in another org
    assert _find_or_provision(org, "o@andere.be", "O") is None     # never moved across orgs


def test_sso_jit_provisions_without_a_seat_limit(app):
    from app.auth.oauth.oidc import _find_or_provision
    from app.auth.service import create_company

    org = create_company("Seat BV", "Owner", "owner@seatbv.be", "geheim123").organization
    _enable_sso(org, ["seatbv.be"])
    db.session.commit()
    user = _find_or_provision(org, "second@seatbv.be", "Second")
    assert user is not None and user.organization_id == org.id


def test_sso_config_saved_via_connector(client, app):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    r = client.post("/app/connector", data={
        "oidc_issuer": "https://login.microsoftonline.com/abc/v2.0",
        "oidc_client_id": "sso-cid", "oidc_client_secret": "sso-secret",
        "allowed_domains": "acme.be, dochter.be", "sso_enabled": "on",
    }, follow_redirects=False)
    assert r.status_code == 302
    org = db.session.query(User).filter_by(email="demo@example.com").first().organization
    assert org.sso.oidc_issuer.endswith("/v2.0") and org.sso.enabled is True
    assert "dochter.be" in org.sso.allowed_domains


def test_sso_start_page_and_unknown_domain(client, app):
    assert client.get("/auth/sso").status_code == 200
    r = client.post("/auth/sso", data={"email": "nobody@nodomain.xyz"}, follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]

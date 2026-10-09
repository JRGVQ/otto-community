"""Per-company connector (bring-your-own Microsoft/Google app)."""
from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def test_org_byo_microsoft_overrides_platform(app):
    from app.auth.service import create_company
    from app.connectors import graph_configured_for, microsoft_app, org_uses_own_microsoft
    from app.security.crypto import encrypt

    org = create_company("Conn BV", "Owner", "o@conn.be", "geheim123").organization

    app.config["GRAPH_CONFIGURED"] = False
    assert graph_configured_for(org) is False          # no own creds, no platform app
    app.config["GRAPH_CONFIGURED"] = True
    assert graph_configured_for(org) is True            # platform fallback

    org.graph_app_id = "org-client-id"
    org.graph_tenant_id = "org-tenant"
    org.graph_client_secret_enc = encrypt("org-secret")
    db.session.commit()
    assert org_uses_own_microsoft(org) is True
    assert microsoft_app(org) == ("org-client-id", "org-tenant", "org-secret")  # org app wins


def test_connector_page_owner_can_save(client, app):
    from app.connectors import org_uses_own_microsoft

    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    assert client.get("/app/connector").status_code == 200

    r = client.post("/app/connector", data={
        "graph_app_id": "abc-123", "graph_tenant_id": "tid-9", "graph_client_secret": "supersecret",
    }, follow_redirects=False)
    assert r.status_code == 302

    org = db.session.query(User).filter_by(email="demo@example.com").first().organization
    assert org.graph_app_id == "abc-123" and org_uses_own_microsoft(org) is True

    from app.security.crypto import decrypt
    assert decrypt(org.graph_client_secret_enc) == "supersecret"  # stored encrypted, decrypts back


def test_sync_now_cli_smoke(app):
    """`flask sync-now` runs the pipeline inline and reports counts (no network in demo mode)."""
    from app.auth.service import create_company
    from app.models import MailAccount

    user = create_company("Sync BV", "Owner", "o@syncbv.be", "geheim123")
    db.session.add(MailAccount(user_id=user.id, provider="outlook", email="o@syncbv.be", status="connected"))
    db.session.commit()

    out = app.test_cli_runner().invoke(args=["sync-now", "o@syncbv.be"]).output
    assert "Processed" in out


def test_export_org_cli_gdpr(app):
    """GDPR export dumps an org's data as JSON."""
    import json

    from app.auth.service import create_company
    from app.tenancy import add_welcome_inbox

    user = create_company("Export BV", "Owner", "o@exportbv.be", "geheim123")
    add_welcome_inbox(user.organization, user)
    db.session.commit()
    slug = user.organization.slug

    out = app.test_cli_runner().invoke(args=["export-org", slug]).output
    data = json.loads(out[out.index("{"):])
    assert data["organization"]["name"] == "Export BV"
    assert any(u["email"] == "o@exportbv.be" for u in data["users"])
    assert len(data["messages"]) >= 1

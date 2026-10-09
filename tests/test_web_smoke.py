"""Every screen renders 200 with seed data."""
import pytest

PUBLIC = ["/auth/login", "/auth/register", "/auth/sso", "/auth/forgot", "/docs/"]
USER = ["/app/inbox", "/app/outlook", "/app/briefing", "/app/tasks",
        "/app/appointments", "/app/follow-ups", "/app/teams", "/app/crm",
        "/app/categories", "/app/integrations", "/app/settings", "/app/rondleiding"]
ADMIN = ["/admin/overzicht", "/admin/users", "/admin/roles", "/admin/sso",
         "/admin/providers", "/admin/orgcats", "/admin/ai", "/admin/audit",
         "/admin/signatures", "/admin/autoreplies", "/admin/knowledge"]
OWNER = ["/owner/", "/owner/companies", "/owner/companies/new", "/owner/health",
         "/owner/scheduler", "/owner/events", "/owner/logs", "/owner/debug", "/owner/email"]


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", USER + ADMIN + OWNER)
def test_app_pages(auth_client, path):
    assert auth_client.get(path).status_code == 200


def test_company_detail_renders(auth_client, demo_user):
    assert auth_client.get(f"/owner/companies/{demo_user.organization_id}").status_code == 200


def test_inbox_selection_renders(auth_client):
    r = auth_client.get("/app/inbox?cat=alle&m=1")
    assert r.status_code == 200
    assert "Concept-antwoord van Otto" in r.get_data(as_text=True)


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/app/inbox")
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]

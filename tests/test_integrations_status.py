"""Integraties page: live (derived) status for To Do/Outlook + the real test probe."""
from app.extensions import db


def test_live_tiles_follow_mailbox_connection(auth_client):
    """Demo user has a connected Microsoft mailbox → To Do/Outlook tiles show connected,
    with a working test action; unbuilt integrations stay honest ('Binnenkort')."""
    page = auth_client.get("/app/integrations").get_data(as_text=True)
    assert "Gekoppeld ✓" in page
    assert "Binnenkort" in page          # Slack/Notion/CRM are not faked
    assert "Test verbinding" in page     # live To Do probe is offered


def test_live_tiles_without_mailbox(client, app):
    from app.auth.service import create_company

    user = create_company("Fresh BV", "Owner", "owner@freshbv.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    client.post("/auth/login", data={"email": "owner@freshbv.be", "password": "geheim123"})

    page = client.get("/app/integrations").get_data(as_text=True)
    assert "Koppel eerst je Microsoft 365-postvak hierboven." in page
    assert "Niet gekoppeld" in page      # live tiles: honest 'not connected', not 'Binnenkort'

    r = client.post("/api/integrations/todo/test")
    assert r.status_code == 400          # no mailbox to probe


def test_todo_test_probe_demo_account(auth_client):
    r = auth_client.post("/api/integrations/todo/test")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["connection_ok"] is True


def test_toggle_reflects_real_state(auth_client):
    live = auth_client.post("/api/integrations/todo/toggle").get_json()
    assert live["connected"] is True     # rides on the connected demo mailbox
    soon = auth_client.post("/api/integrations/slack/toggle").get_json()
    assert soon["connected"] is False
    assert "binnenkort" in soon["toast"].lower()

"""Per-organisation AI training opt-in (default off)."""
from app.extensions import db
from app.seed.demo_data import DEMO_PASSWORD


def _login_owner(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


# ── Training opt-in ───────────────────────────────────────────────────────────
def test_training_optin_default_off(app, demo_user):
    """A fresh org never allows training until the admin explicitly opts in."""
    assert demo_user.organization.allows_ai_training is False


def test_training_optin_toggle_on_and_off(client, app, demo_user):
    _login_owner(client)
    org_id = demo_user.organization.id

    r = client.post("/api/admin/ai/toggle", json={"key": "allow_training"})
    assert r.status_code == 200 and r.get_json()["on"] is True
    db.session.expire_all()
    from app.models import Organization
    assert db.session.get(Organization, org_id).allows_ai_training is True

    r = client.post("/api/admin/ai/toggle", json={"key": "allow_training"})
    assert r.status_code == 200 and r.get_json()["on"] is False
    db.session.expire_all()
    assert db.session.get(Organization, org_id).allows_ai_training is False

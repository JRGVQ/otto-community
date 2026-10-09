"""Phase-1 batch: numbered 7-category taxonomy, profile page, demo-cleanup CLI."""
from app.extensions import db
from app.models import Organization, User


def test_seed_creates_seven_categories(app):
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    keys = [c.key for c in sorted(org.categories, key=lambda c: c.position)]
    assert len(keys) == 7
    assert "vergaderingen" in keys and "financieel" in keys
    assert keys[0] == "te_beantwoorden"  # positional order drives the 1..7 numbering


def test_categories_page_shows_numbers(auth_client):
    body = auth_client.get("/app/categories").get_data(as_text=True)
    assert "1." in body and "Vergaderingen" in body  # numbered list incl. a new category


def test_profile_merged_into_settings_and_saves(auth_client):
    # Profiel is merged into Instellingen — the old URL redirects there.
    r = auth_client.get("/app/profile")
    assert r.status_code == 302 and "/app/settings" in r.headers["Location"]

    body = auth_client.get("/app/settings").get_data(as_text=True)
    assert "Standaard antwoord" in body and "Handtekening" in body
    assert "to the point" in body  # seeded default_reply is pre-filled

    auth_client.post("/app/settings/voorkeuren",
                     data={"default_reply": "Kort en zakelijk.", "signature": "Groeten,\nDemo"})
    db.session.expire_all()
    u = db.session.query(User).filter_by(email="demo@example.com").first()
    assert u.style_profile.default_reply == "Kort en zakelijk."
    assert u.style_profile.signature == "Groeten,\nDemo"


def test_demo_owner_seeded_with_instructions(app):
    u = db.session.query(User).filter_by(email="demo@example.com").first()
    assert u.style_profile.default_reply and "to the point" in u.style_profile.default_reply



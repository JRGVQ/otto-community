"""Eén-klik-verbind-popup: bij Verbinden (Microsoft én Gmail) verschijnt eerst een popup
die toont wat er in die ene toestemming allemaal meekomt, met één knop die alles verbindt.
De echte OAuth-flow blijft onveranderd — de popup vervolgt gewoon de originele actie."""
from __future__ import annotations

from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


def _login(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _not_onboarded():
    # /auth/onboarding stuurt een al-onboarded gebruiker meteen door naar de inbox.
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    user.onboarded = False
    db.session.commit()


def _providers_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "GRAPH_CONFIGURED", True)
    monkeypatch.setitem(app.config, "MS_CLIENT_ID", "platform-cid")
    monkeypatch.setitem(app.config, "MS_CLIENT_SECRET", "platform-secret")
    monkeypatch.setitem(app.config, "GMAIL_CONFIGURED", True)
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_ID", "google-cid")
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_SECRET", "google-secret")


def test_onboarding_tiles_open_the_popup_when_ready(client, app, monkeypatch):
    _providers_on(app, monkeypatch)
    _login(client)
    _not_onboarded()

    html = client.get("/auth/onboarding").get_data(as_text=True)

    assert 'data-connect-popup="outlook"' in html
    assert 'data-connect-popup="gmail"' in html
    assert 'id="connect-popup"' in html
    assert "Alles verbinden met één klik" in html


def test_onboarding_tiles_skip_the_popup_when_provider_not_configured(client, app):
    """Zonder geconfigureerde provider stuurt de tegel naar 'Eigen app instellen' —
    daar hoort géén één-klik-belofte bij."""
    _login(client)
    _not_onboarded()

    html = client.get("/auth/onboarding").get_data(as_text=True)

    assert 'data-connect-popup="outlook"' not in html
    assert 'data-connect-popup="gmail"' not in html


def test_integrations_buttons_open_the_popup(client, app, monkeypatch):
    _providers_on(app, monkeypatch)
    _login(client)

    html = client.get("/app/integrations").get_data(as_text=True)

    assert 'data-connect-popup="outlook"' in html
    assert 'data-connect-popup="gmail"' in html
    assert 'id="connect-popup"' in html


def test_popup_lists_what_one_click_connects(client, app, monkeypatch):
    _providers_on(app, monkeypatch)
    _login(client)

    html = client.get("/app/integrations").get_data(as_text=True)

    # Microsoft-lijst
    assert "Agenda: vrije momenten voorstellen" in html
    assert "Microsoft To Do en Planner" in html
    # Gmail-lijst
    assert "met labels in Gmail zelf" in html
    # De belofte + de verzend-garantie
    assert "stelt Otto alles automatisch voor je in" in html
    assert "géén verzendrecht" in html


def test_admin_sees_the_org_wide_option(client, app, monkeypatch):
    """Een beheerder krijgt in de popup de keuze om meteen de hele organisatie goed te
    keuren (zolang dat nog niet gebeurd is)."""
    _providers_on(app, monkeypatch)
    _login(client)

    html = client.get("/app/integrations").get_data(as_text=True)

    assert 'id="connect-popup-admin"' in html
    assert "Meteen goedkeuren voor mijn hele organisatie" in html


def test_org_wide_option_disappears_once_consented(client, app, monkeypatch):
    _providers_on(app, monkeypatch)
    _login(client)
    from app.models.base import utcnow

    org = db.session.query(User).filter_by(email="demo@example.com").first().organization
    org.graph_admin_consented_at = utcnow()
    db.session.commit()

    html = client.get("/app/integrations").get_data(as_text=True)

    assert 'id="connect-popup-admin"' not in html


def test_non_admin_never_sees_the_org_wide_option(client, app, monkeypatch):
    _providers_on(app, monkeypatch)
    _login(client)
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    monkeypatch.setattr(User, "has_perm", lambda self, perm: False)

    html = client.get("/app/integrations").get_data(as_text=True)

    assert user is not None
    assert 'id="connect-popup-admin"' not in html

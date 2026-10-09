"""Outlook-taakvenster: manifest, venster en de context-API.

Het venster leunt volledig op wat Otto al bepaald heeft. De grens die telt: manifest en
venster zijn openbaar (Outlook laadt ze zonder sessie en ze bevatten geen gegevens), de
gegevens komen alleen via /addin/context en alleen voor een ingelogde gebruiker, gescoped
op wat die mag zien.
"""
from __future__ import annotations

from urllib.parse import quote

from app.extensions import db
from app.models import Message, User


# ── Openbare bestanden ───────────────────────────────────────────────────────
def test_the_manifest_is_public_xml(client):
    r = client.get("/addin/manifest.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["Content-Type"]
    body = r.get_data(as_text=True)
    assert "<OfficeApp" in body and "/addin/taskpane" in body


def test_the_taskpane_is_public_and_loads_office_js(client):
    r = client.get("/addin/taskpane")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "appsforoffice.microsoft.com" in body and "Otto" in body


def test_the_taskpane_gets_the_office_friendly_csp(client):
    r = client.get("/addin/taskpane")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "appsforoffice.microsoft.com" in csp
    assert "outlook.office.com" in csp        # frame-ancestors, niet 'self'
    assert "X-Frame-Options" not in r.headers  # anders blokkeert Outlook het frame


# ── De context-API ───────────────────────────────────────────────────────────
def test_context_without_a_session_says_not_authenticated(client):
    r = client.get("/addin/context")
    assert r.status_code == 200
    data = r.get_json()
    assert data["authenticated"] is False


def _visible_categorised_message(email="demo@example.com"):
    me = db.session.query(User).filter_by(email=email).first()
    for m in (db.session.query(Message)
              .filter(Message.organization_id == me.organization_id,
                      Message.category_id.isnot(None))
              .all()):
        if m.visible_to(me):
            return me, m
    return me, None


def test_context_returns_what_otto_knows_about_the_open_mail(app, auth_client):
    me, msg = _visible_categorised_message()
    assert msg is not None, "seed zou een gecategoriseerd bericht moeten hebben"

    url = f"/addin/context?subject={quote(msg.subject or '')}&from={quote(msg.sender_email or '')}"
    r = auth_client.get(url)
    assert r.status_code == 200
    data = r.get_json()
    assert data["authenticated"] is True and data["found"] is True
    assert data["category"]["name"] == msg.category.name
    assert isinstance(data["tasks"], list) and isinstance(data["snippets"], list)


def test_context_for_an_unknown_mail_is_authenticated_but_not_found(app, auth_client):
    r = auth_client.get("/addin/context?subject=" + quote("Iets wat Otto nooit zag zonderzonder"))
    data = r.get_json()
    assert data["authenticated"] is True and data["found"] is False
    # Snippets staan los van of de mail al verwerkt is.
    assert isinstance(data["snippets"], list)

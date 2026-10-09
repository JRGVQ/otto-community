"""WYSIWYG-editor + sjabloongalerij op Beheer → Handtekeningen.

Bewaakte beloftes:
1. de sjabloongalerij levert bruikbare, e-mailveilige HTML met placeholders;
2. het scherm rendert de editor-tab, de galerij en de sjablonen-JSON;
3. een via de editor opgeslagen (vrije HTML) handtekening blijft gewoon werken.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import Signature, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_gallery_templates_are_email_safe_with_placeholders(app):
    from app.signature_templates import gallery

    items = gallery()
    assert len(items) >= 3
    keys = {t["key"] for t in items}
    assert {"klassiek", "modern", "compact"} <= keys
    for t in items:
        assert t["name"] and t["desc"] and t["html"]
        assert "{{naam}}" in t["html"]                 # bevat placeholders
        assert "</script>" not in t["html"].lower()    # veilig om in <script> te embedden
        assert "<script" not in t["html"].lower()      # geen actieve inhoud


def test_signatures_screen_renders_editor_and_gallery(app, auth):
    page = auth.get("/admin/signatures").get_data(as_text=True)
    assert 'data-mode="editor"' in page                # de editor-tab
    assert 'id="sig-editor-area"' in page              # het contenteditable vlak
    assert 'data-action="sig-load-template"' in page   # galerij-knoppen
    assert 'id="sig-templates-data"' in page           # sjablonen-JSON blob
    assert "Begin met een sjabloon" in page


def test_context_exposes_templates(app):
    from app.web.context.admin import signatures_context

    with app.test_request_context():
        from flask_login import login_user

        login_user(_demo_user())
        data = signatures_context()
    assert data["sig_templates"] and data["sig_templates"][0]["html"]


def test_editor_html_signature_saves_and_resolves(app, auth):
    """Een vrije-HTML-handtekening uit de editor (chips al geserialiseerd naar {{key}})
    wordt opgeslagen én per gebruiker gerenderd."""
    user = _demo_user()
    r = auth.post("/admin/signatures/save", data={
        "name": "Editor-test", "domain": "",
        "html": '<div style="color:#333"><b>{{naam}}</b> — {{functie}}</div>',
        "user_ids": [str(user.id)],
    }, follow_redirects=True)
    assert r.status_code == 200
    sig = db.session.query(Signature).filter_by(name="Editor-test").one()
    assert sig.html and "{{naam}}" in sig.html and sig.body  # body afgeleid uit html

    from app.signatures import resolve_rich
    user.job_title = "Zaakvoerder"
    db.session.commit()
    rich = resolve_rich(user)
    assert rich.html and "Zaakvoerder" in rich.html and user.name.split()[0] in rich.html

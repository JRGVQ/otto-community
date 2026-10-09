"""Sjabloon-import + galerij op Beheer → Handtekeningen (Fase 1).

Bewaakte beloftes:
1. sanitize_signature_html maakt geïmporteerde HTML e-mailveilig: <script>/<style>,
   class/id en onveilige URL-schema's eruit, maar style/tabellen/data-ph/{{key}} blijven;
2. gallery(org) voegt de ingebouwde sjablonen samen met de geïmporteerde, per organisatie
   geïsoleerd;
3. de import-route bewaart een gesanitized sjabloon, weigert te grote invoer, en verwijderen
   is beperkt tot de eigen organisatie.
"""
from __future__ import annotations

import io

import pytest

from app.extensions import db
from app.models import SignatureTemplate, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


# ── sanitizer ────────────────────────────────────────────────────────────────
def test_sanitize_signature_html_is_email_safe():
    from app.htmlview import sanitize_signature_html

    raw = (
        '<style>.x{color:red}</style>'
        '<script>alert(1)</script>'
        '<div class="brand" id="top" style="color:#333">'
        '<b>{{naam}}</b> — <span data-ph="functie">{{functie}}</span>'
        '<img src="javascript:evil()" alt="x">'
        '<a href="https://acme.be">site</a>'
        '<table><tr><td>cel</td></tr></table>'
        '</div>'
    )
    out = sanitize_signature_html(raw)
    assert out is not None
    low = out.lower()
    assert "<script" not in low and "<style" not in low   # actieve/CSS-tags weg
    assert "alert(1)" not in low                            # scriptinhoud weg
    assert 'class="brand"' not in low and 'id="top"' not in low  # class/id gestript
    assert "javascript:" not in low                         # onveilig schema geweerd
    assert 'style="color:#333"' in low                      # inline opmaak blijft
    assert 'data-ph="functie"' in low                       # leeg-veld-hook blijft
    assert "{{naam}}" in out and "{{functie}}" in out       # placeholders overleven
    assert "<table" in low and "<td" in low                 # tabel-opmaak blijft


def test_sanitize_signature_html_empty_is_none():
    from app.htmlview import sanitize_signature_html

    assert sanitize_signature_html("") is None
    assert sanitize_signature_html("   ") is None


# ── galerij-merge + org-isolatie ─────────────────────────────────────────────
def test_gallery_merges_builtins_and_custom_per_org(app):
    from app.signature_templates import builtins, gallery

    user = _demo_user()
    org = user.organization
    n_builtin = len(builtins())

    # Zonder org: enkel de ingebouwde.
    assert len(gallery(None)) == n_builtin

    db.session.add(SignatureTemplate(
        organization_id=org.id, slug="eigen", name="Eigen huisstijl",
        html="<div><b>{{naam}}</b></div>"))
    # Een sjabloon van een ándere organisatie mag hier nooit opduiken.
    db.session.add(SignatureTemplate(
        organization_id=org.id + 9999, slug="vreemd", name="Vreemd",
        html="<div>x</div>"))
    db.session.commit()

    items = gallery(org)
    assert len(items) == n_builtin + 1
    custom = [t for t in items if t["source"] == "custom"]
    assert len(custom) == 1
    assert custom[0]["name"] == "Eigen huisstijl"
    assert custom[0]["key"] == f"c{custom[0]['id']}"        # collision-vrije key
    assert custom[0] is items[0]                            # eigen sjablonen bovenaan
    assert all(t["source"] == "builtin" for t in items[1:])


# ── import-route ─────────────────────────────────────────────────────────────
def test_import_paste_saves_sanitized_template(app, auth):
    user = _demo_user()
    r = auth.post("/admin/signatures/templates/import", data={
        "name": "Geplakt sjabloon", "description": "Test",
        "html": '<div class="x" style="color:#111"><b>{{naam}}</b><script>bad()</script></div>',
    }, follow_redirects=True)
    assert r.status_code == 200
    row = db.session.query(SignatureTemplate).filter_by(
        organization_id=user.organization_id, name="Geplakt sjabloon").one()
    assert "{{naam}}" in row.html
    assert "<script" not in row.html.lower() and 'class="x"' not in row.html.lower()
    assert row.slug and row.created_by == user.id


def test_import_upload_saves_template(app, auth):
    user = _demo_user()
    data = {
        "name": "Geüpload",
        "file": (io.BytesIO(b"<p style='margin:0'>{{bedrijf}}</p>"), "sig.html"),
    }
    r = auth.post("/admin/signatures/templates/import", data=data,
                  content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    row = db.session.query(SignatureTemplate).filter_by(
        organization_id=user.organization_id, name="Geüpload").one()
    assert "{{bedrijf}}" in row.html


def test_import_rejects_oversized(app, auth):
    big = "<p>" + ("a" * (256 * 1024 + 10)) + "</p>"
    r = auth.post("/admin/signatures/templates/import", data={
        "name": "Groot", "html": big}, follow_redirects=True)
    assert r.status_code == 200
    assert db.session.query(SignatureTemplate).filter_by(name="Groot").first() is None


def test_import_requires_name(app, auth):
    r = auth.post("/admin/signatures/templates/import", data={
        "html": "<b>{{naam}}</b>"}, follow_redirects=True)
    assert r.status_code == 200
    assert db.session.query(SignatureTemplate).filter(
        SignatureTemplate.html.like("%{{naam}}%")).first() is None


def test_delete_is_scoped_to_own_org(app, auth):
    user = _demo_user()
    mine = SignatureTemplate(organization_id=user.organization_id, slug="mijn",
                             name="Mijn", html="<b>x</b>")
    other = SignatureTemplate(organization_id=user.organization_id + 9999, slug="hun",
                              name="Hun", html="<b>y</b>")
    db.session.add_all([mine, other])
    db.session.commit()
    mine_id, other_id = mine.id, other.id

    # Andermans sjabloon verwijderen → 404, blijft bestaan.
    r = auth.post(f"/admin/signatures/templates/{other_id}/delete")
    assert r.status_code == 404
    assert db.session.get(SignatureTemplate, other_id) is not None

    # Eigen sjabloon verwijderen → weg.
    r = auth.post(f"/admin/signatures/templates/{mine_id}/delete", follow_redirects=True)
    assert r.status_code == 200
    assert db.session.get(SignatureTemplate, mine_id) is None


def test_screen_shows_import_card(app, auth):
    page = auth.get("/admin/signatures").get_data(as_text=True)
    assert "Eigen sjabloon importeren" in page

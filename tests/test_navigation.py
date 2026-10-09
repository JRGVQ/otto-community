"""Navigatie: icoonstrip voor werkomgevingen, iconen en inklapbare groepen.

De zijbalk mengde drie soorten dingen door elkaar — werk, instellingen en account —
die er allemaal identiek uitzagen. Deze tests bewaken de scheiding die dat oplost.
"""
from __future__ import annotations

import re

import pytest

from app.extensions import db
from app.models import User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _html(client, path="/app/inbox"):
    return client.get(path).get_data(as_text=True)


# ── Icoonstrip ───────────────────────────────────────────────────────────────
def test_the_rail_offers_both_workspaces(app, auth):
    html = _html(auth)
    assert 'class="ws-rail"' in html
    assert "/app/inbox" in html and "/admin/overzicht" in html


def test_the_platform_workspace_is_owner_only(app, client):
    """Een gewone gebruiker mag de backoffice niet eens als optie zien."""
    owner = db.session.query(User).filter_by(email="demo@example.com").first()
    owner.is_platform_owner = False
    db.session.commit()
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    assert "/owner/" not in _html(client)

    owner.is_platform_owner = True
    db.session.commit()
    assert "/owner/" in _html(client)


def test_account_items_left_the_screen_list(app, auth):
    """Rondleiding, documentatie en afmelden horen in de strip, niet ertussen."""
    html = _html(auth)
    rail = html[html.index('class="ws-rail"'):html.index('class="sidebar"')]
    for href in ("/app/rondleiding", "/docs/", "/auth/logout"):
        assert href in rail, f"{href} hoort in de strip te staan"

    sidebar = html[html.index('class="sidebar"'):]
    nav = sidebar[:sidebar.index("</div>\n</div>")] if "</div>\n</div>" in sidebar else sidebar
    assert "/auth/logout" not in nav


def test_the_topbar_no_longer_duplicates_the_switch(app, auth):
    """De omgevingswissel stond ook in de topbar; twee plekken voor hetzelfde verwart."""
    html = _html(auth)
    assert '<div class="segmented" style="width:184px;">' not in html


# ── Iconen ───────────────────────────────────────────────────────────────────
def test_every_screen_has_an_icon(app, auth):
    from app.web.context import ADMIN_NAV, USER_NAV

    assert all(item.get("icon") for item in USER_NAV)
    assert all(item.get("icon") for item in ADMIN_NAV)


def test_icons_replaced_the_meaningless_dots(app, auth):
    html = _html(auth)
    sidebar = html[html.index('class="sidebar"'):]
    assert "nav-ico" in sidebar
    assert "dot-sq" not in sidebar   # de gekleurde vierkantjes codeerden niets


def test_icons_are_inline_svg(app, auth):
    """Geen extern bestand: de CSP staat dat niet toe, en een na-ladend icoon springt."""
    html = _html(auth)
    assert re.search(r'<svg class="nav-ico"', html)
    assert "<img" not in html[html.index('class="ws-rail"'):html.index('class="sidebar"')]


# ── Groepen ──────────────────────────────────────────────────────────────────
def test_groups_are_collapsible(app, auth):
    html = _html(auth)
    assert 'data-nav-group="werk"' in html
    assert 'data-nav-group="instellen"' in html
    assert 'data-in-group="werk"' in html


def test_group_keys_are_stable_across_languages(app, auth):
    """De sleutel mag niet het vertaalde label zijn, anders vergeet hij de stand
    zodra iemand van taal wisselt."""
    from app.web.context import ADMIN_NAV, USER_NAV

    for item in [*USER_NAV, *ADMIN_NAV]:
        if item.get("section"):
            key = item.get("section_key")
            assert key and key == key.lower() and " " not in key, item


def test_admin_groups_are_present(app, auth):
    html = _html(auth, "/admin/overzicht")
    for key in ("team", "beveiliging", "ai", "account"):
        assert f'data-nav-group="{key}"' in html


def test_the_collapse_script_is_loaded(app, auth):
    assert "js/nav.js" in _html(auth)


# ── Blijft werken ────────────────────────────────────────────────────────────
def test_the_inbox_badge_survived(app, auth):
    """Het wachtende-mail-getal is een van de weinige echte signalen in de balk."""
    from app.web.context import base_context

    with app.test_request_context():
        from flask_login import login_user

        login_user(db.session.query(User).filter_by(email="demo@example.com").first())
        nav = base_context("user", "inbox")["nav"]
    inbox = next(n for n in nav if n["key"] == "inbox")
    assert "badge" in inbox


def test_every_app_screen_still_renders(app, auth):
    for path in ("/app/inbox", "/app/briefing", "/app/tasks", "/app/categories",
                 "/app/rules", "/app/integrations", "/app/settings",
                 "/admin/overzicht", "/admin/users", "/admin/ai"):
        assert auth.get(path).status_code == 200, path

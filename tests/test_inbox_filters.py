"""Inbox: de filterbalk en het aantal queries dat een lijst kost.

De filterbalk wikkelde tot een rafelig blok van drie rijen; die is nu twee vaste rijen
met de sortering in de kop. En de berichtenlijst haalde per bericht apart zijn concept
op — bij een echte mailbox honderden queries per paginaweergave.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.extensions import db
from app.models import Draft, Message, User
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _html(client, path="/app/inbox"):
    return client.get(path).get_data(as_text=True)


# ── Filterbalk ───────────────────────────────────────────────────────────────
def test_filters_are_chips_without_checkbox_squares(app, auth):
    """De vierkantjes namen breedte in en duwden de rij naar drie regels."""
    html = _html(auth)
    assert 'class="chip' in html
    # De checkbox blijft bestaan om te verzenden, maar wordt visueel weggehaald in CSS.
    assert 'type="checkbox"' in html


def test_sorting_moved_out_of_the_filter_bar(app, auth):
    """Sorteren bepaalt de volgorde, niet wat je ziet — het hoort bij de telling."""
    html = _html(auth)
    filter_bar = html[html.index('class="msg-filter"'):html.index("msg-row")]
    assert "<select" not in filter_bar
    assert "sort-toggle" in html


def test_the_sort_toggle_flips_the_order(app, auth):
    html = _html(auth)
    assert "sort=old" in html          # staat nu op nieuwste → link biedt oudste
    html_old = _html(auth, "/app/inbox?sort=old")
    m = re.search(r'class="sort-toggle" href="([^"]+)"', html_old)
    assert m and "sort=old" not in m.group(1)   # en terug


def test_sorting_survives_filtering(app, auth):
    """Anders verlies je je volgorde zodra je een filter aanzet."""
    html = _html(auth, "/app/inbox?sort=old")
    bar = html[html.index('class="msg-filter"'):html.index("msg-row")]
    assert 'name="sort" value="old"' in bar


def test_filters_survive_sorting(app, auth):
    html = _html(auth, "/app/inbox?unread=1")
    m = re.search(r'class="sort-toggle" href="([^"]+)"', html)
    assert m and "unread=1" in m.group(1)


def test_a_clear_button_appears_only_when_filtering(app, auth):
    assert "chip-clear" not in _html(auth)
    assert "chip-clear" in _html(auth, "/app/inbox?unread=1")


def test_clearing_keeps_the_chosen_order(app, auth):
    """Filters weg, maar je volgorde is een aparte keuze en blijft staan."""
    html = _html(auth, "/app/inbox?unread=1&sort=old")
    m = re.search(r'class="chip chip-clear" href="([^"]+)"', html)
    assert m and "sort=old" in m.group(1) and "unread=1" not in m.group(1)


def test_active_filters_are_marked(app, auth):
    html = _html(auth, "/app/inbox?unread=1")
    assert re.search(r'class="chip on"', html)


# ── Queries per weergave ─────────────────────────────────────────────────────
def _count_queries(client, path):
    seen: list[str] = []

    def cap(conn, cur, stmt, params, ctx, many):
        seen.append(" ".join(stmt.split()))

    event.listen(Engine, "before_cursor_execute", cap)
    try:
        client.get(path)
    finally:
        event.remove(Engine, "before_cursor_execute", cap)

    def table(q):
        m = re.search(r"\bFROM\s+([a-z_]+)", q, re.I)
        return m.group(1) if m else q.split()[0].lower()

    return Counter(table(q) for q in seen)


def _bulk(app, n: int):
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    cats = list(user.organization.categories)
    for i in range(n):
        msg = Message(organization_id=user.organization_id, user_id=user.id,
                      provider_message_id=f"bulk-{i}", sender=f"Klant {i}",
                      sender_email=f"k{i}@voorbeeld.be", subject=f"Vraag {i}",
                      category_id=cats[i % len(cats)].id if cats else None)
        db.session.add(msg)
        db.session.flush()
        db.session.add(Draft(message_id=msg.id, status="concept", current_text="x"))
    db.session.commit()


def test_drafts_are_not_fetched_per_message(app, auth):
    """De kern: het aantal queries mag niet meegroeien met het aantal berichten."""
    before = _count_queries(auth, "/app/inbox")
    _bulk(app, 60)
    after = _count_queries(auth, "/app/inbox")

    assert after["drafts"] == before["drafts"], (
        f"concepten worden per bericht opgehaald: {before['drafts']} → {after['drafts']}"
    )
    assert after["drafts"] <= 3


def test_categories_are_not_fetched_per_message(app, auth):
    before = _count_queries(auth, "/app/inbox")
    _bulk(app, 60)
    after = _count_queries(auth, "/app/inbox")
    assert after["categories"] == before["categories"]


def test_the_whole_page_stays_within_a_query_budget(app, auth):
    """Een vast plafond vangt een nieuwe N+1 op voor ze in productie belandt."""
    _bulk(app, 60)
    total = sum(_count_queries(auth, "/app/inbox").values())
    assert total < 40, f"{total} queries voor één inboxweergave"

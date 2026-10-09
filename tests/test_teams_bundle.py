"""Teams-meldingen bundelen: één overzicht op vaste momenten in plaats van een stroom.

Eén kaart per actionabele mail maakt een druk gedeeld kanaal onbruikbaar. De grens die
de tests bewaken: bundelen mag geen enkele melding verliezen (wat de post niet haalt,
blijft in de wachtrij), en een net aangezette bundel mag niet meteen een berg oude
meldingen de deur uit doen.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app import teams_bundle as B


# ── Momenten uitrekenen ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("9,13,17", [9, 13, 17]),
    (" 17 , 9 , 13 ", [9, 13, 17]),
    ("9,9,13", [9, 13]),
])
def test_hours_are_parsed_and_tidied(raw, expected):
    assert B.parse_hours(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "onzin", "99"])
def test_nonsense_falls_back(raw):
    assert B.parse_hours(raw) == list(B.DEFAULT_HOURS)


def test_due_after_a_moment_has_passed():
    now = datetime(2026, 8, 24, 14, 0)
    assert B.due(now, [9, 13, 17], datetime(2026, 8, 24, 9, 30)) is True    # 13:00 gepasseerd
    assert B.due(now, [9, 13, 17], datetime(2026, 8, 24, 13, 30)) is False  # nog niets nieuws


def test_a_freshly_enabled_bundle_does_not_flush_immediately():
    """last_flush None → wachten op het eerstvolgende moment, niet meteen legen."""
    assert B.due(datetime(2026, 8, 24, 14, 0), [9, 13, 17], None) is False


def test_before_the_first_moment_yesterdays_last_moment_still_counts():
    """Om 07:00 is het laatste moment gisteren 17:00. Wie daarvoor flushte (16:00) heeft
    dat moment gemist en is nu aan de beurt; wie erna flushte (20:00) niet."""
    now = datetime(2026, 8, 24, 7, 0)
    assert B.due(now, [9, 13, 17], datetime(2026, 8, 23, 16, 0)) is True
    assert B.due(now, [9, 13, 17], datetime(2026, 8, 23, 20, 0)) is False


# ── De kaart ─────────────────────────────────────────────────────────────────
def test_the_card_lists_every_item_when_there_are_few():
    kaart = B.summary_card("Support", [
        {"subject": "Vraag A", "sender": "jan@x"},
        {"subject": "Vraag B", "sender": ""},
    ])
    tekst = kaart["sections"][0]["text"]
    assert "Vraag A" in tekst and "jan@x" in tekst and "Vraag B" in tekst
    assert "2 nieuwe mail in Support" in kaart["summary"]


def test_one_item_reads_as_singular():
    kaart = B.summary_card("Support", [{"subject": "Vraag", "sender": "jan@x"}])
    assert "Nieuwe mail in Support" in kaart["summary"] and "1 nieuwe" not in kaart["summary"]


def test_a_flood_is_capped_and_counted():
    items = [{"subject": f"Vraag {i}", "sender": "x"} for i in range(25)]
    tekst = B.summary_card("Support", items)["sections"][0]["text"]
    assert "en nog 15" in tekst          # 25 − 10 getoond
    assert tekst.count("Vraag") == 10


# ── De wachtrij en de flush ──────────────────────────────────────────────────
@pytest.fixture()
def shared(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import MailAccount, MailboxMember, MailboxSettings

    owner = create_company("Acme BV", "Owner", "owner@acme-tb.be", "geheim123")
    db.session.commit()
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="support@acme-tb.be", status="connected",
                       mailbox_type="shared")
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxSettings(
        mail_account_id=acct.id, display_name="Support",
        teams_webhook_url="https://webhook/x", teams_notify=True, teams_bundle=True,
    ))
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=owner.id, role="agent"))
    db.session.commit()
    return acct


class _Msg:
    def __init__(self, acct, subject="Vraag", sender="jan@klant.be", category=None):
        self.account = acct
        self.account_id = acct.id
        self.subject = subject
        self.sender = sender
        self.sender_email = sender
        self.category = category


def test_with_bundling_on_a_new_mail_is_queued_not_posted(app, shared, monkeypatch):
    from app.extensions import db
    from app.models import TeamsPending
    from app.tasks import teams_notify as T

    def boom(*a, **k):
        raise AssertionError("bij bundelen mag er niets meteen gepost worden")

    monkeypatch.setattr(T, "_post", boom)
    assert T.notify_new_mail(_Msg(shared)) is True
    assert db.session.query(TeamsPending).filter_by(mail_account_id=shared.id).count() == 1


def test_without_bundling_a_new_mail_is_posted_immediately(app, shared, monkeypatch):
    from app.extensions import db
    from app.models import TeamsPending
    from app.tasks import teams_notify as T

    shared.settings.teams_bundle = False
    db.session.commit()
    posts = []
    monkeypatch.setattr(T, "_post", lambda url, card: posts.append(card) or True)

    assert T.notify_new_mail(_Msg(shared)) is True
    assert len(posts) == 1
    assert db.session.query(TeamsPending).count() == 0


def _flush(monkeypatch, posts, now):
    from app.models.base import utcnow as _u  # noqa: F401
    from app.tasks import teams_notify as T

    monkeypatch.setattr(T, "_post", lambda url, card: posts.append(card) or True)
    monkeypatch.setattr("app.tasks.teams_notify.utcnow", lambda: now, raising=False)
    # utcnow wordt binnen flush_bundles geïmporteerd uit app.models.base; patch daar.
    monkeypatch.setattr("app.models.base.utcnow", lambda: now)
    return T.flush_bundles()


def test_the_first_flush_only_sets_the_clock(app, shared, monkeypatch):
    """Nog nooit geflusht → geen kaart, alleen het startpunt zetten."""
    from app.extensions import db
    from app.models import TeamsPending

    db.session.add(TeamsPending(mail_account_id=shared.id, subject="Oud", sender="x"))
    db.session.commit()
    posts = []
    n = _flush(monkeypatch, posts, datetime(2026, 8, 24, 14, 0))

    assert n == 0 and posts == []
    assert shared.settings.teams_last_flush_at is not None
    # De oude melding blijft staan tot het eerstvolgende moment.
    assert db.session.query(TeamsPending).count() == 1


def test_a_passed_moment_posts_one_card_and_clears_the_queue(app, shared, monkeypatch):
    from app.extensions import db
    from app.models import TeamsPending

    shared.settings.teams_last_flush_at = datetime(2026, 8, 24, 9, 30)
    for i in range(3):
        db.session.add(TeamsPending(mail_account_id=shared.id, subject=f"Vraag {i}", sender="x"))
    db.session.commit()

    posts = []
    n = _flush(monkeypatch, posts, datetime(2026, 8, 24, 14, 0))    # 13:00 gepasseerd

    assert n == 1 and len(posts) == 1
    assert "3 nieuwe mail in Support" in posts[0]["summary"]
    assert db.session.query(TeamsPending).count() == 0


def test_a_failed_post_keeps_the_queue(app, shared, monkeypatch):
    """Wat de webhook niet haalt, mag niet verdwijnen — het gaat mee met de volgende ronde."""
    from app.extensions import db
    from app.models import TeamsPending
    from app.tasks import teams_notify as T

    shared.settings.teams_last_flush_at = datetime(2026, 8, 24, 9, 30)
    db.session.add(TeamsPending(mail_account_id=shared.id, subject="Vraag", sender="x"))
    db.session.commit()

    monkeypatch.setattr("app.models.base.utcnow", lambda: datetime(2026, 8, 24, 14, 0))
    monkeypatch.setattr(T, "_post", lambda url, card: False)   # webhook plat
    n = T.flush_bundles()

    assert n == 0
    assert db.session.query(TeamsPending).count() == 1        # nog steeds in de wachtrij


def test_nothing_flushes_before_a_moment(app, shared, monkeypatch):
    from app.extensions import db
    from app.models import TeamsPending

    shared.settings.teams_last_flush_at = datetime(2026, 8, 24, 13, 30)
    db.session.add(TeamsPending(mail_account_id=shared.id, subject="Vraag", sender="x"))
    db.session.commit()

    posts = []
    n = _flush(monkeypatch, posts, datetime(2026, 8, 24, 14, 0))   # niks nieuws sinds 13:30
    assert n == 0 and posts == []
    assert db.session.query(TeamsPending).count() == 1

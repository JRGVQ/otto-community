"""Digest-modus: de inbox als een vaste stapel in plaats van een lopende band.

De grens die hier telt: er mag niets zoekraken. Wat wacht, moet altijd met één klik te
zien zijn, en een verkeerd ingevulde instelling mag nooit een lege inbox opleveren.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import inbox_digest as D
from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, Message, StyleProfile

TZ = ZoneInfo("Europe/Brussels")


class _Msg:
    """Genoeg van een bericht voor de splitsing — die kijkt alleen naar received_at."""

    def __init__(self, received_at):
        self.received_at = received_at


# ── Momenten uitrekenen ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("9,17", [9, 17]),
    (" 9 , 17 ", [9, 17]),
    ("17,9", [9, 17]),          # gesorteerd
    ("9,9,17", [9, 17]),        # ontdubbeld
    ("9;17", [9, 17]),          # puntkomma mag ook
])
def test_hours_are_parsed_and_tidied(raw, expected):
    assert D.parse_hours(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "onzin", "25", "-3", "9u,17u"])
def test_nonsense_falls_back_instead_of_emptying_the_inbox(raw):
    """Een verkeerd getal in een instelling mag nooit betekenen dat je niets meer ziet."""
    assert D.parse_hours(raw) == list(D.DEFAULT_HOURS)


def test_too_many_moments_is_no_longer_a_digest():
    assert len(D.parse_hours("1,2,3,4,5,6,7,8,9")) == D.MAX_SLOTS


def test_the_last_slot_is_the_most_recent_one_today():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=TZ)
    assert D.last_slot(now, [9, 17]) == datetime(2026, 8, 21, 9, 0, tzinfo=TZ)


def test_before_the_first_moment_you_get_yesterdays_last(app):
    now = datetime(2026, 8, 21, 7, 0, tzinfo=TZ)
    assert D.last_slot(now, [9, 17]) == datetime(2026, 8, 20, 17, 0, tzinfo=TZ)


def test_the_next_slot_rolls_over_to_tomorrow():
    now = datetime(2026, 8, 21, 19, 0, tzinfo=TZ)
    assert D.next_slot(now, [9, 17]) == datetime(2026, 8, 22, 9, 0, tzinfo=TZ)
    assert D.next_slot(datetime(2026, 8, 21, 10, 0, tzinfo=TZ), [9, 17]) \
        == datetime(2026, 8, 21, 17, 0, tzinfo=TZ)


# ── Splitsen ─────────────────────────────────────────────────────────────────
def test_mail_from_before_the_cutoff_is_shown_and_the_rest_waits():
    cutoff = datetime(2026, 8, 21, 9, 0, tzinfo=TZ)
    oud = _Msg(datetime(2026, 8, 21, 8, 0, tzinfo=TZ))
    net = _Msg(datetime(2026, 8, 21, 9, 0, tzinfo=TZ))       # precies op het moment
    nieuw = _Msg(datetime(2026, 8, 21, 10, 0, tzinfo=TZ))

    shown, held = D.split([oud, net, nieuw], cutoff)
    assert shown == [oud, net] and held == [nieuw]


def test_a_message_without_a_timestamp_is_always_shown():
    """Liever een mail te veel in de stapel dan een mail die nergens meer opduikt."""
    cutoff = datetime(2026, 8, 21, 9, 0, tzinfo=TZ)
    leeg = _Msg(None)
    shown, held = D.split([leeg], cutoff)
    assert shown == [leeg] and held == []


def test_naive_timestamps_from_the_database_still_compare():
    cutoff = datetime(2026, 8, 21, 9, 0, tzinfo=TZ)
    naief = _Msg(datetime(2026, 8, 21, 8, 0))   # zoals SQLite het teruggeeft
    shown, held = D.split([naief], cutoff)
    assert shown == [naief] and held == []


# ── In het scherm ────────────────────────────────────────────────────────────
@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-dig.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    if user.style_profile is None:
        db.session.add(StyleProfile(user_id=user.id))
    db.session.commit()
    return user


def _inbox_with_two_mails(owner):
    """Eén bericht van vóór het moment en één van erna.

    De momenten worden op middernacht gezet en de tijdstippen worden uit de lokale klok
    afgeleid, zodat deze test om drie uur 's nachts hetzelfde doet als om vijf uur 's
    middags. Een test die van het uur afhangt, zakt ooit in CI om een reden die niets
    met de code te maken heeft."""
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="owner@acme-dig.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    lokaal_nu = datetime.now(TZ).replace(tzinfo=None)   # zoals de database het bewaart
    for subject, moment in (("Van eerder", lokaal_nu - timedelta(days=2)),
                            ("Net binnen", lokaal_nu)):
        db.session.add(Message(
            organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
            sender="Klant", sender_email="jan@klant.be", subject=subject,
            body="Vraagje.", received_at=moment,
        ))
    db.session.commit()


def _login(client):
    return client.post("/auth/login", data={"email": "owner@acme-dig.be", "password": "geheim123"})


def test_the_inbox_is_untouched_when_the_mode_is_off(client, app, owner):
    _inbox_with_two_mails(owner)
    _login(client)
    page = client.get("/app/inbox").get_data(as_text=True)
    assert "Net binnen" in page and "Van eerder" in page
    assert "wachten op je volgende overzicht" not in page


def test_fresh_mail_waits_and_can_always_be_shown(client, app, owner):
    _inbox_with_two_mails(owner)
    owner.style_profile.digest_mode = True
    owner.style_profile.digest_hours = "0"      # het moment van vandaag ligt op middernacht
    db.session.commit()
    _login(client)

    held = client.get("/app/inbox").get_data(as_text=True)
    assert "Net binnen" not in held             # kwam ná middernacht binnen
    assert "Van eerder" in held                 # stond al in de stapel
    assert "wachten op je volgende overzicht" in held

    # De uitweg: één klik en alles staat er weer.
    alles = client.get("/app/inbox?alles=1").get_data(as_text=True)
    assert "Net binnen" in alles and "wachten op je volgende overzicht" not in alles


def test_the_moments_are_saved_and_normalised(client, app, owner):
    _login(client)
    client.post("/app/settings/voorkeuren",
                data={"digest_mode": "on", "digest_hours": "17, 9, onzin"}, follow_redirects=True)
    style = db.session.get(StyleProfile, owner.style_profile.id)
    assert style.digest_mode is True and style.digest_hours == "9,17"

    client.post("/app/settings/voorkeuren", data={}, follow_redirects=True)
    style = db.session.get(StyleProfile, owner.style_profile.id)
    assert style.digest_mode is False and style.digest_hours is None

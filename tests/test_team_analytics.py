"""Analyse per gedeelde mailbox (Teams fase 5, tweede helft).

De cijfers moeten kloppen én de mailbox-grens respecteren: alleen leden zien ze, en er
wordt uitsluitend geteld wat in díé mailbox zit.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.auth.service import create_company
from app.constants import DRAFT_CONCEPT, DRAFT_SENT
from app.extensions import db
from app.models import Draft, MailAccount, MailboxMember, MailboxSettings, Message, Role, User
from app.models.base import utcnow
from app.models.user import initials_for


@pytest.fixture()
def owner(app):
    u = create_company("Acme BV", "Owner", "owner@acme-ta.be", "geheim123")
    u.email_verified = True
    u.onboarded = True
    # Teams is Enterprise-only (24/08); deze schermen vereisen het plan.
    if u.organization.license:
        u.organization.license.plan = "Enterprise"
    db.session.commit()
    return u


def _member(org, email, name="Collega"):
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    u = User(email=email, name=name, initials=initials_for(name), avatar_color="#111",
             organization_id=org.id, role_id=role.id if role else None, status="Actief",
             email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()
    return u


def _shared(owner, email="team@acme-ta.be", members=(), sla=None):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected", mailbox_type="shared")
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxSettings(mail_account_id=acct.id, sla_minutes=sla))
    for u in (owner, *members):
        db.session.add(MailboxMember(mail_account_id=acct.id, user_id=u.id, role="agent"))
    db.session.commit()
    return acct


def _mail(acct, owner, *, received_ago_min=60, replied_after_min=None,
          state="open", assignee=None, unread=False, draft_status=None):
    now = utcnow()
    msg = Message(
        organization_id=acct.organization_id, user_id=owner.id, account_id=acct.id,
        sender="Klant", sender_email="k@voorbeeld.be", subject="Vraag",
        received_at=now - timedelta(minutes=received_ago_min),
        state=state, unread=unread,
        assignee_user_id=assignee.id if assignee else None,
    )
    if replied_after_min is not None:
        msg.replied_at = msg.received_at + timedelta(minutes=replied_after_min)
    db.session.add(msg)
    db.session.flush()
    if draft_status:
        db.session.add(Draft(message_id=msg.id, status=draft_status, current_text="x"))
    db.session.commit()
    return msg


def _ctx(app, acct, as_user):
    """De contextbouwer draait op current_user; log dus eerst in."""
    from flask_login import login_user

    from app.web.context import team_analytics_context

    with app.test_request_context():
        login_user(as_user)
        return team_analytics_context(acct)


# ── Volume ───────────────────────────────────────────────────────────────────
def test_counts_this_week_versus_last(app, owner):
    acct = _shared(owner)
    for _ in range(3):
        _mail(acct, owner, received_ago_min=60)                 # deze week
    for _ in range(2):
        _mail(acct, owner, received_ago_min=10 * 24 * 60)       # vorige week

    d = _ctx(app, acct, owner)
    assert d["total"] == 5
    assert d["this_week"] == 3 and d["last_week"] == 2
    assert d["trend"] == 50  # 3 tegenover 2


def test_trend_is_none_without_a_previous_week(app, owner):
    acct = _shared(owner)
    _mail(acct, owner)
    assert _ctx(app, acct, owner)["trend"] is None  # geen deling door nul


# ── Reactietijd ──────────────────────────────────────────────────────────────
def test_response_time_is_the_median_not_the_average(app, owner):
    """Eén uitschieter mag het cijfer niet onbruikbaar maken."""
    acct = _shared(owner)
    for minutes in (10, 20, 30):
        _mail(acct, owner, replied_after_min=minutes)
    _mail(acct, owner, replied_after_min=100000)  # blijft weken liggen

    d = _ctx(app, acct, owner)
    assert d["replied_count"] == 4
    assert d["median_response"] == "25 min"  # mediaan van 10/20/30/100000


def test_unanswered_mail_is_left_out_of_the_response_time(app, owner):
    acct = _shared(owner)
    _mail(acct, owner, replied_after_min=30)
    _mail(acct, owner)  # nooit beantwoord
    d = _ctx(app, acct, owner)
    assert d["replied_count"] == 1 and d["median_response"] == "30 min"


def test_response_time_is_dash_without_data(app, owner):
    acct = _shared(owner)
    assert _ctx(app, acct, owner)["median_response"] == "—"


def test_long_response_times_are_shown_in_hours(app, owner):
    acct = _shared(owner)
    _mail(acct, owner, received_ago_min=5000, replied_after_min=195)  # 3u15
    assert _ctx(app, acct, owner)["median_response"] == "3 u 15"


# ── SLA ──────────────────────────────────────────────────────────────────────
def test_sla_percentage_counts_replies_within_the_target(app, owner):
    acct = _shared(owner, sla=60)
    _mail(acct, owner, replied_after_min=30)    # binnen
    _mail(acct, owner, replied_after_min=45)    # binnen
    _mail(acct, owner, replied_after_min=120)   # te laat
    _mail(acct, owner, replied_after_min=90)    # te laat

    d = _ctx(app, acct, owner)
    assert d["sla_minutes"] == 60 and d["sla_pct"] == 50


def test_sla_is_none_when_no_target_is_set(app, owner):
    acct = _shared(owner)  # geen sla_minutes
    _mail(acct, owner, replied_after_min=30)
    assert _ctx(app, acct, owner)["sla_pct"] is None


def test_a_reply_exactly_on_the_target_counts_as_within(app, owner):
    acct = _shared(owner, sla=60)
    _mail(acct, owner, replied_after_min=60)
    assert _ctx(app, acct, owner)["sla_pct"] == 100


# ── Status en concepten ──────────────────────────────────────────────────────
def test_open_work_is_grouped_by_state(app, owner):
    acct = _shared(owner)
    _mail(acct, owner, state="open")
    _mail(acct, owner, state="in_progress")
    _mail(acct, owner, state="done")
    _mail(acct, owner, state="done")

    counts = {s["key"]: s["count"] for s in _ctx(app, acct, owner)["by_state"]}
    assert counts == {"open": 1, "in_progress": 1, "awaiting_reply": 0, "done": 2}


def test_draft_counts_are_reported(app, owner):
    acct = _shared(owner)
    _mail(acct, owner, draft_status=DRAFT_CONCEPT)
    _mail(acct, owner, draft_status=DRAFT_SENT)
    _mail(acct, owner, draft_status=DRAFT_SENT)
    d = _ctx(app, acct, owner)["draft_counts"]
    assert d["concept"] == 1 and d["verzonden"] == 2


# ── Werkverdeling ────────────────────────────────────────────────────────────
def test_work_is_split_per_member(app, owner):
    org = owner.organization
    sam = _member(org, "sam@acme-ta.be", "Sam Jansen")
    acct = _shared(owner, members=[sam])

    _mail(acct, owner, assignee=sam, replied_after_min=10)
    _mail(acct, owner, assignee=sam, state="done")
    _mail(acct, owner, assignee=owner)
    _mail(acct, owner)  # niemand

    d = _ctx(app, acct, owner)
    per_name = {m["name"]: m for m in d["by_member"]}
    assert per_name["Sam Jansen"]["assigned"] == 2
    assert per_name["Sam Jansen"]["replied"] == 1
    assert per_name["Sam Jansen"]["open"] == 1     # de afgehandelde telt niet mee
    assert d["unassigned"] == 1


def test_members_are_sorted_by_workload(app, owner):
    org = owner.organization
    sam = _member(org, "sam@acme-ta.be", "Sam")
    acct = _shared(owner, members=[sam])
    _mail(acct, owner, assignee=sam)
    _mail(acct, owner, assignee=sam)
    _mail(acct, owner, assignee=owner)
    assert _ctx(app, acct, owner)["by_member"][0]["name"] == "Sam"


# ── Grenzen ──────────────────────────────────────────────────────────────────
def test_only_this_mailbox_is_counted(app, owner):
    support = _shared(owner, "support@acme-ta.be")
    sales = _shared(owner, "sales@acme-ta.be")
    _mail(support, owner)
    _mail(support, owner)
    _mail(sales, owner)

    assert _ctx(app, support, owner)["total"] == 2
    assert _ctx(app, sales, owner)["total"] == 1


# ── Scherm ───────────────────────────────────────────────────────────────────
def _login(client, email="owner@acme-ta.be"):
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return client


def test_screen_renders_for_a_member(app, client, owner):
    acct = _shared(owner, sla=60)
    _mail(acct, owner, replied_after_min=30, draft_status=DRAFT_CONCEPT)
    html = _login(client).get(f"/app/teams/{acct.id}/analyse").get_data(as_text=True)
    assert "Werkverdeling" in html and "binnen de SLA" in html


def test_screen_is_closed_to_non_members(app, client, owner):
    outsider = _member(owner.organization, "buiten@acme-ta.be")
    acct = _shared(owner, "support@acme-ta.be")   # outsider is geen lid
    resp = _login(client, outsider.email).get(f"/app/teams/{acct.id}/analyse")
    assert resp.status_code in (403, 404)


def test_screen_is_closed_to_another_organisation(app, client, owner):
    other = create_company("Rivaal NV", "R", "r@rivaal-ta.be", "geheim123")
    other.email_verified = True
    other.onboarded = True
    db.session.commit()
    acct = _shared(owner)
    resp = _login(client, other.email).get(f"/app/teams/{acct.id}/analyse")
    assert resp.status_code in (403, 404)


def test_team_page_links_to_the_analysis(app, client, owner):
    acct = _shared(owner)
    html = _login(client).get(f"/app/teams/{acct.id}/settings").get_data(as_text=True)
    assert f"/app/teams/{acct.id}/analyse" in html

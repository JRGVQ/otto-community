"""Received-time parsing/formatting + that ingestion stops stamping everything 'nu'."""
import re
from datetime import datetime, timedelta

from app.extensions import db
from app.models import MailAccount, User
from app.providers.dto import MessageDTO
from app.tasks.sync import _persist
from app.timefmt import parse_epoch_ms, parse_graph_dt, reltime


def test_parse_graph_dt_to_naive_utc():
    dt = parse_graph_dt("2026-06-27T08:42:00Z")
    assert dt == datetime(2026, 6, 27, 8, 42, 0) and dt.tzinfo is None
    assert parse_graph_dt(None) is None
    assert parse_graph_dt("garbage") is None


def test_parse_epoch_ms_to_naive_utc():
    assert parse_epoch_ms("1750000000000") == datetime.utcfromtimestamp(1750000000)
    assert parse_epoch_ms(None) is None
    assert parse_epoch_ms("x") is None


def test_reltime_labels(app):
    now = datetime.utcnow()
    assert reltime(None, "fallback") == "fallback"          # seed rows keep their string
    assert re.match(r"^\d{2}:\d{2}$", reltime(now))          # today → HH:MM
    assert reltime(now - timedelta(days=1)) == "gisteren"
    assert reltime(now - timedelta(days=3)) in {"ma", "di", "wo", "do", "vr", "za", "zo"}
    assert re.match(r"^\d{2}/\d{2}$", reltime(now - timedelta(days=30)))  # older → DD/MM


def _account(app):
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email=user.email, status="connected")
    db.session.add(acct)
    db.session.flush()
    return acct


def _dto(received_at, mid="m-1"):
    return MessageDTO(provider_message_id=mid, thread_id="t1", sender="Charlotte Parys",
                      sender_email="c@belnot.be", subject="Volmacht", snippet="…",
                      body="Hallo", received_at=received_at)


def test_persist_uses_real_received_time_not_now(app):
    acct = _account(app)
    msg = _persist(acct, _dto(datetime(2026, 6, 20, 9, 15)))
    assert msg.received_at == datetime(2026, 6, 20, 9, 15)
    assert msg.time_display != "nu"                          # the bug: it used to be "nu"


def test_persist_backfills_legacy_nu_rows(app):
    acct = _account(app)
    legacy = _persist(acct, _dto(None, mid="m-legacy"))      # no timestamp → "nu"
    assert legacy.time_display == "nu" and legacy.received_at is None
    # A later import of the same message now carries a real time → backfilled, not re-added.
    again = _persist(acct, _dto(datetime(2026, 6, 18, 7, 30), mid="m-legacy"))
    assert again is None                                     # same row, not a duplicate
    assert legacy.received_at == datetime(2026, 6, 18, 7, 30) and legacy.time_display != "nu"

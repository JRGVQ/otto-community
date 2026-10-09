"""Inbox mirrors the live mailbox INBOX (removed mail drops out), and at most one concept
draft per conversation."""
from __future__ import annotations

from app.extensions import db
from app.models import Category, Draft, MailAccount, Message


def _account(demo_user):
    acct = MailAccount(user_id=demo_user.id, organization_id=demo_user.organization_id,
                       provider="outlook", email="box@example.be", status="connected")
    db.session.add(acct)
    db.session.flush()
    return acct


def _msg(demo_user, acct, pmid, **kw):
    m = Message(organization_id=demo_user.organization_id, user_id=demo_user.id,
                account_id=acct.id, provider_message_id=pmid,
                sender="A", sender_email="a@example.be", initials="A", avatar_color="#000",
                time_display="nu", subject="Onderwerp", snippet="…", body="Body",
                lang="antwoord in Nederlands", **kw)
    db.session.add(m)
    db.session.flush()
    return m


# ── Change 1: inbox mirrors the live INBOX ────────────────────────────────────
def test_removed_message_is_archived(app, demo_user):
    from app.tasks.sync import _archive_removed

    acct = _account(demo_user)
    m = _msg(demo_user, acct, "pm-del")
    db.session.commit()
    assert m.archived is False

    _archive_removed(acct, "pm-del")          # left the INBOX on the server
    db.session.refresh(m)
    assert m.archived is True                 # drops out of the inbox view


def test_seed_message_is_never_archived_by_removal(app, demo_user):
    from app.tasks.sync import _archive_removed

    acct = _account(demo_user)
    m = _msg(demo_user, acct, "pm-seed", is_seed=True)
    db.session.commit()
    _archive_removed(acct, "pm-seed")
    db.session.refresh(m)
    assert m.archived is False                # seed/sample rows are protected


def test_returned_message_is_unarchived_on_resync(app, demo_user):
    from app.providers.dto import MessageDTO
    from app.tasks.sync import _persist

    acct = _account(demo_user)
    m = _msg(demo_user, acct, "pm-back", archived=True)
    db.session.commit()

    dto = MessageDTO(provider_message_id="pm-back", thread_id=None, sender="A",
                     sender_email="a@example.be", subject="s", snippet="s", body="b")
    _persist(acct, dto)                       # existing row seen again in the INBOX delta
    db.session.commit()                       # sync_account commits after the fetch loop
    db.session.refresh(m)
    assert m.archived is False                # back in the inbox


# ── Change 2: one concept per conversation ────────────────────────────────────
def _draftable_category(org_id) -> Category:
    return (db.session.query(Category)
            .filter(Category.organization_id == org_id, Category.draft_enabled.is_(True))
            .first())


def test_only_one_concept_per_thread(app, demo_user):
    from app.tasks.draft import generate_draft

    acct = _account(demo_user)
    cat = _draftable_category(demo_user.organization_id)
    m1 = _msg(demo_user, acct, "pm-1", provider_thread_id="conv-1", category_id=cat.id)
    m2 = _msg(demo_user, acct, "pm-2", provider_thread_id="conv-1", category_id=cat.id)
    db.session.commit()

    did1 = generate_draft(m1.id, force=True)          # first concept for the conversation
    assert did1 is not None
    did2 = generate_draft(m2.id)                        # second inbound in same thread
    assert did2 == did1                                 # reused, not a new concept
    db.session.refresh(m2)
    assert m2.draft is None
    assert (db.session.query(Draft)
            .filter(Draft.message_id.in_([m1.id, m2.id]))
            .count()) == 1                              # exactly one concept in the thread

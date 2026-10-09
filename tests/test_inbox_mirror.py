"""Spiegel: Otto's overzicht volgt de werkelijke Inbox, en één concept per mail.

Twee beloftes:
1. mail die in Outlook opgeborgen is, verdwijnt uit het overzicht — óók als Otto de
   delta-melding daarover gemist heeft;
2. er staat nooit meer dan één Otto-concept per mail in de Concepten-map.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import Draft, MailAccount, Message, User
from app.tasks.sync import reconcile_inbox
from app.tasks.writeback import write_draft


@pytest.fixture()
def account(app):
    user = db.session.query(User).filter_by(email="demo@example.com").first()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="demo@example.com", status="connected")
    db.session.add(acct)
    db.session.commit()
    return acct


def _mail(acct, pid, *, archived=False, seed=False):
    user = db.session.get(User, acct.user_id)
    msg = Message(organization_id=acct.organization_id, user_id=user.id, account_id=acct.id,
                  provider_message_id=pid, sender="Klant", sender_email="k@v.be",
                  subject=f"Mail {pid}", archived=archived, is_seed=seed)
    db.session.add(msg)
    db.session.commit()
    return msg


class _Provider:
    """Minimale nep-provider; alleen wat de spiegel en de concepten nodig hebben."""

    def __init__(self, inbox_ids=None, drafts=None, fail_inventory=False):
        self._inbox = inbox_ids
        self.own_drafts = list(drafts or [])
        self.deleted: list[str] = []
        self.created = 0
        self.marked: list[str] = []
        self._fail = fail_inventory

    def is_configured(self):
        return True

    def inbox_message_ids(self):
        return None if self._fail else set(self._inbox or [])

    def find_own_drafts(self, conversation_id):
        return list(self.own_drafts)

    def delete_draft(self, draft_id):
        self.deleted.append(draft_id)
        if draft_id in self.own_drafts:
            self.own_drafts.remove(draft_id)

    def create_draft(self, **kw):
        self.created += 1
        new_id = f"nieuw-{self.created}"
        self.own_drafts.append(new_id)
        return new_id

    def mark_own_draft(self, draft_id):
        self.marked.append(draft_id)


# ── Spiegel ──────────────────────────────────────────────────────────────────
def test_mail_that_left_the_inbox_is_archived(app, account):
    """De kern: opgeborgen mail verdwijnt uit het overzicht, ook zonder delta-melding."""
    blijft = _mail(account, "in-1")
    weg = _mail(account, "opgeborgen-1")

    assert reconcile_inbox(account, _Provider(inbox_ids=["in-1"])) == 1
    db.session.refresh(blijft), db.session.refresh(weg)
    assert blijft.archived is False
    assert weg.archived is True


def test_reconciliation_is_idempotent(app, account):
    _mail(account, "opgeborgen-1")
    p = _Provider(inbox_ids=[])
    assert reconcile_inbox(account, p) == 1
    assert reconcile_inbox(account, p) == 0  # tweede ronde vindt niets meer


def test_seed_mail_is_never_archived(app, account):
    """Voorbeeldmail hoort niet bij de mailbox en mag niet verdwijnen."""
    voorbeeld = _mail(account, "seed-1", seed=True)
    assert reconcile_inbox(account, _Provider(inbox_ids=[])) == 0
    db.session.refresh(voorbeeld)
    assert voorbeeld.archived is False


def test_nothing_is_archived_when_the_inventory_fails(app, account):
    """Kan de stand niet bepaald worden, dan nooit gokken — anders wist je de hele inbox."""
    msg = _mail(account, "in-1")
    assert reconcile_inbox(account, _Provider(fail_inventory=True)) == 0
    db.session.refresh(msg)
    assert msg.archived is False


def test_other_mailboxes_are_left_alone(app, account):
    user = db.session.get(User, account.user_id)
    andere = MailAccount(user_id=user.id, organization_id=account.organization_id,
                         provider="outlook", email="tweede@example.com", status="connected")
    db.session.add(andere)
    db.session.commit()
    van_andere = _mail(andere, "andere-1")

    reconcile_inbox(account, _Provider(inbox_ids=[]))
    db.session.refresh(van_andere)
    assert van_andere.archived is False


def test_already_archived_mail_is_not_recounted(app, account):
    _mail(account, "oud-1", archived=True)
    assert reconcile_inbox(account, _Provider(inbox_ids=[])) == 0


# ── Eén concept per mail ─────────────────────────────────────────────────────
def _draft_for(account, monkeypatch, provider):
    msg = _mail(account, "mail-1")
    msg.provider_thread_id = "conv-1"
    draft = Draft(message_id=msg.id, current_text="Beste, …", status="concept")
    db.session.add(draft)
    db.session.commit()
    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: provider)
    return draft


def test_previous_draft_is_removed_before_a_new_one(app, account, monkeypatch):
    p = _Provider()
    draft = _draft_for(account, monkeypatch, p)
    first = write_draft(draft)
    second = write_draft(draft)

    assert first in p.deleted           # het eerste concept is opgeruimd
    assert draft.provider_draft_id == second
    assert len(p.own_drafts) == 1       # er staat er maar één in Outlook


def test_orphaned_drafts_are_cleaned_up(app, account, monkeypatch):
    """Het geval waardoor ze stapelden: een concept in Outlook dat Otto niet kende."""
    p = _Provider(drafts=["wees-1", "wees-2"])
    draft = _draft_for(account, monkeypatch, p)   # Otto kent geen enkel id
    write_draft(draft)

    assert "wees-1" in p.deleted and "wees-2" in p.deleted
    assert len(p.own_drafts) == 1


def test_the_draft_id_is_stored_immediately(app, account, monkeypatch):
    """Zodat een fout verderop het concept nooit meer onvindbaar maakt."""
    p = _Provider()

    def boom(draft_id):
        raise RuntimeError("merkteken mislukt")

    monkeypatch.setattr(p, "mark_own_draft", boom)
    draft = _draft_for(account, monkeypatch, p)
    write_draft(draft)

    db.session.refresh(draft)
    assert draft.provider_draft_id == "nieuw-1"   # ondanks de fout erna vastgelegd


def test_own_drafts_are_marked(app, account, monkeypatch):
    p = _Provider()
    draft = _draft_for(account, monkeypatch, p)
    new_id = write_draft(draft)
    assert p.marked == [new_id]


def test_writeback_is_a_no_op_without_a_provider(app, account, monkeypatch):
    class _Unconfigured(_Provider):
        def is_configured(self):
            return False

    p = _Unconfigured()
    draft = _draft_for(account, monkeypatch, p)
    assert write_draft(draft) is None
    assert p.created == 0

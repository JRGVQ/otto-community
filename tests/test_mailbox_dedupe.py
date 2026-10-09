"""Eén postvak, één regel.

Opnieuw koppelen hoorde altijd al hetzelfde postvak te zijn, maar maakte een nieuwe rij
aan. Dat is niet alleen cosmetisch: de synchronisatie ontdubbelt per postvak, dus vier
koppelingen leverden elk bericht vier keer op.

De grens die deze tests bewaken: samenvoegen mag nooit werk weggooien. Een dubbel dat
onaangeroerd is mag weg; zodra er een concept, notitie, taak of feedback aan hangt, blijft
het staan.
"""
from __future__ import annotations

import pytest

from app import mailboxes
from app.auth.service import create_company
from app.extensions import db
from app.models import (
    Draft,
    FollowUp,
    MailAccount,
    MailboxMember,
    MailboxSettings,
    Message,
    MessageNote,
    Task,
)


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-dup.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user


def _account(owner, email="owner@acme-dup.be", provider="outlook", **kw):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider=provider, email=email, status="connected", **kw)
    db.session.add(acct)
    db.session.commit()
    return acct


def _message(owner, acct, pmid="AAA-1", **kw):
    msg = Message(organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
                  sender="Klant", sender_email="jan@klant.be", subject="Vraag",
                  body="Tekst.", provider_message_id=pmid, **kw)
    db.session.add(msg)
    db.session.commit()
    return msg


# ── Samenvoegen ──────────────────────────────────────────────────────────────
def test_four_connections_become_one(app, owner):
    """Precies het geval uit het scherm: vier keer hetzelfde adres."""
    for _ in range(4):
        _account(owner)

    merged, _msgs = mailboxes.merge_duplicates(owner)

    assert merged == 3
    rest = db.session.query(MailAccount).filter_by(user_id=owner.id).all()
    assert len(rest) == 1
    assert db.session.get(type(owner), owner.id).mailbox_count == 1


def test_the_newest_connection_survives(app, owner):
    """Die heeft de verste tokens — de oudste zou binnenkort verlopen."""
    oud, nieuw = _account(owner), _account(owner)
    mailboxes.merge_duplicates(owner)

    assert db.session.get(MailAccount, nieuw.id) is not None
    assert db.session.get(MailAccount, oud.id) is None


def test_addresses_that_differ_only_in_case_are_the_same_mailbox(app, owner):
    """Microsoft geeft de UPN soms met een hoofdletter terug."""
    _account(owner, email="Owner@Acme-Dup.be")
    _account(owner, email="owner@acme-dup.be")

    merged, _ = mailboxes.merge_duplicates(owner)
    assert merged == 1


def test_different_addresses_are_left_alone(app, owner):
    _account(owner, email="owner@acme-dup.be")
    _account(owner, email="tweede@acme-dup.be")

    merged, _ = mailboxes.merge_duplicates(owner)
    mijne = db.session.query(MailAccount).filter_by(user_id=owner.id).all()
    assert merged == 0 and len(mijne) == 2


def test_a_shared_mailbox_is_never_merged_into_a_personal_one(app, owner):
    """Een gedeeld postvak hoort bij een ander adres en een andere flow."""
    _account(owner)
    gedeeld = _account(owner, email="support@acme-dup.be", mailbox_type="shared")
    db.session.add(MailboxSettings(mail_account_id=gedeeld.id))
    db.session.add(MailboxMember(mail_account_id=gedeeld.id, user_id=owner.id, role="agent"))
    db.session.commit()

    merged, _ = mailboxes.merge_duplicates(owner)
    assert merged == 0 and db.session.get(MailAccount, gedeeld.id) is not None


# ── Wat er meeverhuist ───────────────────────────────────────────────────────
def test_everything_hanging_off_a_duplicate_moves_to_the_survivor(app, owner):
    oud, nieuw = _account(owner), _account(owner)
    msg = _message(owner, oud)
    fu = FollowUp(organization_id=owner.organization_id, user_id=owner.id,
                  account_id=oud.id, subject="Nog geen antwoord", current_text="Ping.")
    db.session.add(fu)
    db.session.commit()

    mailboxes.merge_duplicates(owner)

    assert db.session.get(Message, msg.id).account_id == nieuw.id
    assert db.session.get(FollowUp, fu.id).account_id == nieuw.id


def test_a_shared_mailbox_keeps_its_route_after_the_merge(app, owner):
    """Het gedeelde postvak werd via een van de dubbels bereikt; die route mag niet
    wegvallen bij het opruimen."""
    oud, nieuw = _account(owner), _account(owner)
    gedeeld = _account(owner, email="support@acme-dup.be", mailbox_type="shared",
                       delegated_via_account_id=oud.id)

    mailboxes.merge_duplicates(owner)

    assert db.session.get(MailAccount, gedeeld.id).delegated_via_account_id == nieuw.id


# ── Dubbele berichten: alleen onaangeroerde kopieën ──────────────────────────
def test_the_same_mail_imported_twice_is_reduced_to_one(app, owner):
    oud, nieuw = _account(owner), _account(owner)
    _message(owner, oud, pmid="AAA-1")
    _message(owner, nieuw, pmid="AAA-1")

    _merged, removed = mailboxes.merge_duplicates(owner)

    assert removed == 1
    assert db.session.query(Message).filter_by(
        user_id=owner.id, provider_message_id="AAA-1").count() == 1


def test_a_copy_with_a_draft_is_never_thrown_away(app, owner):
    """Werk mag niet verdwijnen, ook niet als het aan de 'verkeerde' kopie hing."""
    oud, nieuw = _account(owner), _account(owner)
    met_concept = _message(owner, oud, pmid="AAA-1")
    db.session.add(Draft(message_id=met_concept.id, status="concept"))
    db.session.commit()
    _message(owner, nieuw, pmid="AAA-1")

    _merged, removed = mailboxes.merge_duplicates(owner)

    assert removed == 1
    overblijver = db.session.query(Message).filter_by(
        user_id=owner.id, provider_message_id="AAA-1").one()
    assert overblijver.id == met_concept.id


@pytest.mark.parametrize("kind", ["notitie", "taak", "toegewezen"])
def test_a_copy_someone_worked_on_is_never_thrown_away(app, owner, kind):
    oud, nieuw = _account(owner), _account(owner)
    aangeraakt = _message(owner, oud, pmid="AAA-1")
    if kind == "notitie":
        db.session.add(MessageNote(message_id=aangeraakt.id,
                                   organization_id=owner.organization_id,
                                   author_user_id=owner.id, body="Gebeld."))
    elif kind == "taak":
        db.session.add(Task(organization_id=owner.organization_id, user_id=owner.id,
                            title="Opvolgen", linked_message_id=aangeraakt.id))
    else:
        aangeraakt.assignee_user_id = owner.id
    db.session.commit()
    _message(owner, nieuw, pmid="AAA-1")

    mailboxes.merge_duplicates(owner)

    assert db.session.get(Message, aangeraakt.id) is not None


def test_two_copies_that_were_both_worked_on_both_stay(app, owner):
    """Bij twijfel liever een dubbel bericht te veel dan iemands werk kwijt."""
    oud, nieuw = _account(owner), _account(owner)
    een = _message(owner, oud, pmid="AAA-1", assignee_user_id=owner.id)
    twee = _message(owner, nieuw, pmid="AAA-1", assignee_user_id=owner.id)

    _merged, removed = mailboxes.merge_duplicates(owner)

    assert removed == 0
    assert db.session.get(Message, een.id) and db.session.get(Message, twee.id)


def test_different_mails_are_not_touched(app, owner):
    oud, nieuw = _account(owner), _account(owner)
    _message(owner, oud, pmid="AAA-1")
    _message(owner, nieuw, pmid="AAA-2")

    _merged, removed = mailboxes.merge_duplicates(owner)
    mijne = db.session.query(Message).filter_by(user_id=owner.id).count()
    assert removed == 0 and mijne == 2


def test_nothing_happens_when_there_is_nothing_to_merge(app, owner):
    _account(owner)
    assert mailboxes.merge_duplicates(owner) == (0, 0)

"""Dubbele postvakkoppelingen samenvoegen.

`finish_onboarding` maakte vroeger bij élke koppeling een nieuwe rij aan, dus wie twee
keer op "Koppelen" klikte — of opnieuw koppelde na een verlopen token — hield hetzelfde
adres meerdere keren over. Dat is nu bij de bron opgelost, maar de rijen die er al staan
verdwijnen daar niet vanzelf door. En het is niet alleen cosmetisch: de synchronisatie
ontdubbelt *per postvak* (`_persist` filtert op `account_id`), dus vier koppelingen
betekent elk bericht vier keer in de inbox.

Wat hier gebeurt is bewust voorzichtig. Berichten worden alleen verwijderd als het
aantoonbaar onaangeroerde kopieën zijn: hetzelfde `provider_message_id`, zonder concept,
zonder notitie, zonder gekoppelde taak en zonder feedback. Alles waar ooit iemand iets
mee gedaan heeft, blijft staan — liever een dubbel bericht te veel dan één stuk werk
kwijt.
"""
from __future__ import annotations

from app.extensions import db
from app.models import (
    Draft,
    Feedback,
    FollowUp,
    KnowledgeEntry,
    MailAccount,
    MailboxCategory,
    MailboxMember,
    MailboxSettings,
    MailRule,
    Message,
    MessageNote,
    Snippet,
    Task,
)

# (model, kolomnaam) van alles dat naar een postvak wijst. Staat hier expliciet in plaats
# van via reflectie: een nieuwe tabel die naar mail_accounts wijst hoort een bewuste keuze
# te zijn in deze lijst, niet iets dat stilzwijgend meeverhuist.
_CHILDREN = (
    (Message, "account_id"),
    (FollowUp, "account_id"),
    (MailRule, "account_id"),
    (Snippet, "account_id"),
    (KnowledgeEntry, "account_id"),
    (MailboxMember, "mail_account_id"),
    (MailboxCategory, "mail_account_id"),
)


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def _groups(user) -> list[list[MailAccount]]:
    """Persoonlijke postvakken van deze gebruiker, gegroepeerd per (provider, adres).
    Alleen groepen met meer dan één rij komen terug."""
    buckets: dict[tuple[str, str], list[MailAccount]] = {}
    for acct in db.session.query(MailAccount).filter_by(user_id=user.id).order_by(MailAccount.id):
        if acct.is_shared:
            continue  # een gedeeld postvak is een ander adres en een andere flow
        buckets.setdefault((acct.provider or "", _norm(acct.email)), []).append(acct)
    return [rows for rows in buckets.values() if len(rows) > 1]


def _untouched(message: Message) -> bool:
    """Is dit bericht een onaangeroerde importkopie? Alleen zulke mogen weg."""
    if db.session.query(Draft.id).filter_by(message_id=message.id).first():
        return False
    if db.session.query(MessageNote.id).filter_by(message_id=message.id).first():
        return False
    if db.session.query(Task.id).filter_by(linked_message_id=message.id).first():
        return False
    if db.session.query(Feedback.id).filter_by(message_id=message.id).first():
        return False
    return not (message.assignee_user_id or message.replied_at)


def _dedupe_messages(account: MailAccount) -> int:
    """Verwijder onaangeroerde dubbels binnen één postvak. Geeft het aantal terug."""
    seen: dict[str, Message] = {}
    removed = 0
    rows = (
        db.session.query(Message)
        .filter(Message.account_id == account.id, Message.provider_message_id.isnot(None))
        .order_by(Message.id)
        .all()
    )
    for msg in rows:
        keeper = seen.get(msg.provider_message_id)
        if keeper is None:
            seen[msg.provider_message_id] = msg
            continue
        # Het bericht waar iemand aan gewerkt heeft, wint — ook als het later kwam.
        if _untouched(msg):
            db.session.delete(msg)
            removed += 1
        elif _untouched(keeper):
            db.session.delete(keeper)
            seen[msg.provider_message_id] = msg
            removed += 1
    return removed


def merge_duplicates(user) -> tuple[int, int]:
    """Voeg de dubbele postvakken van `user` samen.

    Geeft (samengevoegde postvakken, verwijderde dubbele berichten) terug. Het postvak
    met het hoogste id blijft staan: dat is de laatste koppeling en heeft dus de verste
    tokens. Alles wat aan de andere rijen hing, verhuist mee."""
    merged = messages_removed = 0
    for rows in _groups(user):
        keeper = rows[-1]
        losers = rows[:-1]
        loser_ids = [a.id for a in losers]

        for model, column in _CHILDREN:
            db.session.query(model).filter(getattr(model, column).in_(loser_ids)).update(
                {getattr(model, column): keeper.id}, synchronize_session=False
            )
        # Een gedeeld postvak dat via een van deze rijen bereikt werd, wijst voortaan
        # naar de blijver — anders verliest het zijn toegang bij het verwijderen.
        db.session.query(MailAccount).filter(
            MailAccount.delegated_via_account_id.in_(loser_ids)
        ).update({MailAccount.delegated_via_account_id: keeper.id}, synchronize_session=False)
        # mailbox_settings heeft een unieke sleutel op het postvak, dus verhuizen kan niet:
        # de blijver houdt zijn eigen rij en die van de dubbels gaat weg.
        db.session.query(MailboxSettings).filter(
            MailboxSettings.mail_account_id.in_(loser_ids)
        ).delete(synchronize_session=False)

        for acct in losers:
            db.session.delete(acct)
        merged += len(losers)
        db.session.flush()
        messages_removed += _dedupe_messages(keeper)

    if merged:
        user.mailbox_count = sum(
            1 for a in db.session.query(MailAccount).filter_by(user_id=user.id).all()
            if not a.is_shared
        )
        db.session.commit()
    return merged, messages_removed

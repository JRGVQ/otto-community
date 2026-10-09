"""Snippets ophalen en beheren.

Welke blokken iemand ziet hangt af van waar hij antwoordt: de organisatiebrede blokken
altijd, plus die van de mailbox waarin hij zit. Alles is gescoped op de organisatie van
de gebruiker, en mailboxgebonden blokken alleen voor wie die mailbox mag zien.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Snippet


def for_account(org, account=None) -> list[Snippet]:
    """Organisatiebrede blokken + die van deze mailbox, in beheervolgorde."""
    if org is None:
        return []
    q = db.session.query(Snippet).filter(Snippet.organization_id == org.id)
    if account is not None:
        q = q.filter((Snippet.account_id.is_(None)) | (Snippet.account_id == account.id))
    else:
        q = q.filter(Snippet.account_id.is_(None))
    return q.order_by(Snippet.account_id.isnot(None), Snippet.position, Snippet.id).all()


def for_message(user, message) -> list[Snippet]:
    """De blokken die bruikbaar zijn bij dit bericht."""
    org = getattr(user, "organization", None)
    account = getattr(message, "account", None) if message is not None else None
    return for_account(org, account)


def manageable(user, snippet) -> bool:
    """Alleen binnen de eigen organisatie, en voor een mailboxblok ook alleen als de
    gebruiker die mailbox mag zien — anders zou een lid van team A die van B kunnen wissen."""
    if snippet is None or snippet.organization_id != getattr(user, "organization_id", None):
        return False
    if snippet.account_id is None:
        return True
    acct = snippet.account
    if acct is None:
        return False
    return user.id in acct.member_user_ids if acct.is_shared else acct.user_id == user.id


def create(org, *, title: str, body: str, account=None) -> Snippet | None:
    title, body = (title or "").strip()[:120], (body or "").strip()
    if not (title and body):
        return None
    account_id = account.id if account is not None else None
    last = (
        db.session.query(Snippet)
        .filter_by(organization_id=org.id, account_id=account_id)
        .order_by(Snippet.position.desc())
        .first()
    )
    snippet = Snippet(
        organization_id=org.id, account_id=account_id, title=title, body=body,
        position=((last.position or 0) + 1) if last else 0,
    )
    db.session.add(snippet)
    db.session.commit()
    return snippet


def mark_used(snippet) -> None:
    """Tel één gebruik. Atomair, zodat gelijktijdig invoegen niets kwijtspeelt."""
    db.session.query(Snippet).filter_by(id=snippet.id).update(
        {Snippet.used_count: Snippet.used_count + 1}, synchronize_session=False
    )
    db.session.commit()

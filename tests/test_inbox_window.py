"""De inbox laadt begrensd in, maar de badges blijven kloppen.

Voorheen werd élk niet-gearchiveerd bericht ingeladen — met vier relaties én de volledige
mailtekst — omdat de categorie-tellingen in Python uit diezelfde lijst kwamen. Daardoor
kon de lijst niet begrensd worden zonder de tellers te vervalsen. Nu komen de tellingen
uit SQL en staat het venster daar los van.
"""
from __future__ import annotations

from flask_login import login_user

from app.extensions import db
from app.models import MailAccount, Message, User
from app.web.context._base import (
    LIST_WINDOW, _category_count_map, _personal_messages, personal_scope,
)


def _owner(app):
    return db.session.query(User).filter_by(email="demo@example.com").first()


def _bulk(user, n, cat_id=None):
    acct = (db.session.query(MailAccount)
            .filter_by(user_id=user.id).filter(MailAccount.mailbox_type != "shared").first())
    assert acct is not None
    for i in range(n):
        db.session.add(Message(organization_id=user.organization_id, user_id=user.id,
                               account_id=acct.id, sender="Bulk", sender_email="bulk@x.be",
                               subject=f"Bulkbericht {i}", snippet="x", category_id=cat_id))
    db.session.commit()


def test_the_list_is_capped_but_the_counts_are_not(app):
    u = _owner(app)
    with app.test_request_context():
        login_user(u)
        voor = _category_count_map(personal_scope())[None]
        _bulk(u, LIST_WINDOW + 25)
        na = _category_count_map(personal_scope())[None]

        assert na == voor + LIST_WINDOW + 25          # telling ziet ALLES
        assert len(_personal_messages()) == LIST_WINDOW  # lijst blijft begrensd


def test_search_reaches_beyond_the_window(app):
    """Zoeken gaat mee de query in, anders zou een oude mail onvindbaar zijn."""
    u = _owner(app)
    with app.test_request_context():
        login_user(u)
        acct = (db.session.query(MailAccount)
                .filter_by(user_id=u.id).filter(MailAccount.mailbox_type != "shared").first())
        naald = Message(organization_id=u.organization_id, user_id=u.id, account_id=acct.id,
                        sender="Oud", sender_email="oud@x.be",
                        subject="Speldenprik in de hooiberg", snippet="x")
        db.session.add(naald)
        db.session.commit()
        _bulk(u, LIST_WINDOW + 50)      # duwt de naald ver buiten het venster

        assert naald.id not in {m.id for m in _personal_messages()}
        gevonden = _personal_messages(search="Speldenprik")
        assert naald.id in {m.id for m in gevonden}


def test_message_bodies_are_not_loaded_for_the_list(app):
    """De lijst toont afzender/onderwerp/snippet — nooit de volledige mailtekst."""
    from sqlalchemy import inspect as sa_inspect

    u = _owner(app)
    with app.test_request_context():
        login_user(u)
        msgs = _personal_messages()
        assert msgs, "demo-org heeft mail nodig voor deze test"
        uitgesteld = sa_inspect(msgs[0]).unloaded
        assert "body" in uitgesteld and "body_html" in uitgesteld

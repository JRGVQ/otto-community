"""Betere concepten: schrijfstijl uit échte verzonden mail + geheugen per afzender.

Twee klachten van de gebruiker zaten hierachter:
1. de schrijfstijl veranderde nooit mee met wat je werkelijk verstuurt — `deep=True` werd
   alléén door de handmatige knop aangeroepen, nergens automatisch;
2. Otto keek niet in de mailbox naar wat er eerder met deze afzender besproken was.
"""
from __future__ import annotations

from app.constants import DRAFT_SENT
from app.extensions import db
from app.models import Draft, MailAccount, Message


def _account(user, email="eigen@kantoor.be"):
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email=email, status="connected",
                       mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    return acct


def _mail(user, acct, *, subject, body, sender_email, thread=None, reply=None):
    m = Message(organization_id=user.organization_id, user_id=user.id, account_id=acct.id,
                sender="Klant", sender_email=sender_email, subject=subject, body=body,
                provider_thread_id=thread)
    db.session.add(m)
    db.session.flush()
    if reply:
        db.session.add(Draft(message_id=m.id, status=DRAFT_SENT, current_text=reply))
    db.session.commit()
    return m


# ── Geheugen per afzender ────────────────────────────────────────────────────
def test_earlier_exchange_with_the_same_sender_reaches_the_draft(app, demo_user):
    """Wat je eerder aan déze persoon antwoordde is de rijkste grond voor een concept."""
    from app.tasks.draft import _sender_history

    acct = _account(demo_user)
    _mail(demo_user, acct, subject="Tarief", body="Wat kost een dossier?",
          sender_email="klant@voorbeeld.be", thread="oud-1",
          reply="Ons uurtarief is 180 euro exclusief btw.")
    nieuw = _mail(demo_user, acct, subject="Nog een vraag", body="En voor een advies?",
                  sender_email="klant@voorbeeld.be", thread="nieuw-1")

    hist = _sender_history(nieuw)
    assert hist is not None
    assert "180 euro" in hist            # jouw eigen antwoord van toen
    assert "Wat kost een dossier" in hist


def test_history_excludes_the_current_conversation(app, demo_user):
    """De lopende conversatie zit al in het transcript — niet dubbel meesturen."""
    from app.tasks.draft import _sender_history

    acct = _account(demo_user, "b@kantoor.be")
    _mail(demo_user, acct, subject="Zelfde draad", body="eerste bericht",
          sender_email="zelfde@voorbeeld.be", thread="draad-1")
    nieuw = _mail(demo_user, acct, subject="Zelfde draad", body="tweede bericht",
                  sender_email="zelfde@voorbeeld.be", thread="draad-1")
    assert _sender_history(nieuw) is None


def test_history_never_crosses_mailboxes(app, demo_user):
    """Mail van een collega mag nooit in jouw concept belanden."""
    from app.models import User
    from app.tasks.draft import _sender_history

    collega = (db.session.query(User)
               .filter(User.organization_id == demo_user.organization_id,
                       User.id != demo_user.id).first())
    assert collega is not None
    mijn = _account(demo_user, "mijn@kantoor.be")
    hun = _account(collega, "hun@kantoor.be")

    _mail(collega, hun, subject="Vertrouwelijk", body="geheime afspraak",
          sender_email="gedeeld@voorbeeld.be", thread="hun-1",
          reply="Dit is het antwoord van mijn collega.")
    mijn_mail = _mail(demo_user, mijn, subject="Vraag", body="iets anders",
                      sender_email="gedeeld@voorbeeld.be", thread="mijn-1")

    assert _sender_history(mijn_mail) is None   # zelfde afzender, ander postvak


def test_the_history_is_wrapped_as_data(app):
    """Eerdere mail is onvertrouwde invoer en mag nooit als instructie gelden."""
    from app.ai.prompts import draft_user

    p = draft_user("Jan", "Onderwerp", "body", "Groeten",
                   history="[01-01-2026] Tarief\n  jij: 180 euro")
    assert "180 euro" in p
    assert "nooit een toezegging" in p


# ── Stijl wordt automatisch bijgewerkt ───────────────────────────────────────
def test_style_profiles_refresh_without_pressing_a_button(app, demo_user, monkeypatch):
    """Regressie: `deep=True` stond alléén achter de handmatige knop, dus de schrijfstijl
    veranderde nooit mee met wat je werkelijk verstuurde."""
    from app.tasks.learn import refresh_style_profiles

    _account(demo_user, "stijl@kantoor.be")

    class _Provider:
        def is_configured(self):
            return True

        def list_recent_sent(self, days=120, limit=40):
            return [{"body": "Dag Peter\n\nIn orde, ik bevestig dat.\n\nGroetjes\nSam"}]

    monkeypatch.setattr("app.providers.get_provider", lambda a: _Provider())
    assert refresh_style_profiles() >= 1
    db.session.expire_all()
    assert demo_user.style_profile.raw_profile.get("description")

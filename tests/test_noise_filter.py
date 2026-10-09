"""Ruisfilter: bulkmail wegzetten zónder de AI aan te roepen.

Wat deze tests vooral bewaken is de reden waarom het filter bestaat: er mag geen enkele
LLM-aanroep meer gebeuren voor een nieuwsbrief. En de andere kant daarvan — een gewone
klantmail mag er nooit in verdwijnen, want dat is een veel duurdere fout dan een
nieuwsbrief die alsnog door de AI gaat.
"""
from __future__ import annotations

import pytest

from app import noise
from app.auth.service import create_company
from app.extensions import db
from app.models import Category, MailAccount, MailboxMember, MailboxSettings, Message, StyleProfile


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner", "owner@acme-ruis.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    if user.style_profile is None:
        db.session.add(StyleProfile(user_id=user.id))
    db.session.commit()
    return user


def _account(owner, *, shared=False, email="owner@acme-ruis.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id, provider="outlook",
                       email=email, status="connected",
                       mailbox_type="shared" if shared else "personal")
    db.session.add(acct)
    db.session.flush()
    if shared:
        db.session.add(MailboxSettings(mail_account_id=acct.id))
        db.session.add(MailboxMember(mail_account_id=acct.id, user_id=owner.id, role="agent"))
    db.session.commit()
    return acct


def _noise_category(org, **kw):
    cat = Category(organization_id=org.id, key="ruis", name="Ruis", ai="Nieuwsbrieven.",
                   is_noise=True, position=99, **kw)
    db.session.add(cat)
    db.session.commit()
    return cat


def _message(owner, acct, **kw):
    fields = {
        "sender": "Nieuwsbrief", "sender_email": "news@example.com",
        "subject": "Onze aanbiedingen van deze week", "body": "Bekijk de aanbiedingen.",
    }
    fields.update(kw)
    msg = Message(organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
                  **fields)
    db.session.add(msg)
    db.session.commit()
    return msg


def _enable(acct, on=True):
    if acct.is_shared:
        acct.settings.noise_filter_enabled = on
    else:
        acct.user.style_profile.noise_filter_enabled = on
    db.session.commit()


# ── Herkennen ────────────────────────────────────────────────────────────────
def test_a_list_unsubscribe_header_marks_bulk(app, owner):
    acct = _account(owner)
    assert noise.looks_like_bulk(_message(owner, acct, unsubscribe_url="https://x/uit"))
    assert noise.looks_like_bulk(_message(owner, acct, unsubscribe_mailto="mailto:uit@x"))


@pytest.mark.parametrize("address", [
    "no-reply@example.com", "noreply@example.com", "NoReply@Example.com",
    "do-not-reply@example.com", "donotreply@example.com", "mailer-daemon@example.com",
])
def test_a_noreply_sender_marks_bulk(app, owner, address):
    acct = _account(owner)
    assert noise.looks_like_bulk(_message(owner, acct, sender_email=address))


@pytest.mark.parametrize("address", [
    "jan@klant.be", "info@klant.be", "reply@klant.be", "noreplynot@klant.be",
])
def test_an_ordinary_sender_is_not_bulk(app, owner, address):
    acct = _account(owner)
    assert not noise.looks_like_bulk(_message(owner, acct, sender_email=address))


def test_nothing_is_bulk_without_a_message(app):
    assert noise.looks_like_bulk(None) is False


# ── Waar het om draait: geen AI-aanroep ──────────────────────────────────────
def test_bulk_mail_never_reaches_the_llm(app, owner, monkeypatch):
    """Het hele punt van het filter. Wordt de classificatie hier toch aangeroepen, dan
    kost elke nieuwsbrief nog steeds tokens en is de functie zinloos."""
    import app.tasks.categorize as C

    acct = _account(owner)
    _enable(acct)
    cat = _noise_category(owner.organization)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    def boom(*a, **k):
        raise AssertionError("de LLM mag hier niet aangeroepen worden")

    monkeypatch.setattr(C, "get_service_for", boom)
    result = C.categorize_message(msg.id)

    assert result["category_key"] == "ruis" and result["source"] == "noise"
    assert db.session.get(Message, msg.id).category_id == cat.id


def test_verbruik_wordt_niet_geboekt_voor_ruis(app, owner, monkeypatch):
    import app.tasks.categorize as C

    acct = _account(owner)
    _enable(acct)
    _noise_category(owner.organization)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    def boom(*a, **k):
        raise AssertionError("er valt niets te meten als er niets verbruikt is")

    monkeypatch.setattr(C, "record_usage", boom)
    C.categorize_message(msg.id)


def test_noise_never_gets_a_draft(app, owner, monkeypatch):
    import app.tasks.pipeline as P

    acct = _account(owner)
    _enable(acct)
    # Zelfs als de categorie ooit op "maakt concepten" stond: ruis wint.
    cat = _noise_category(owner.organization, draft_enabled=True)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    def boom(*a, **k):
        raise AssertionError("ruis vraagt geen concept")

    monkeypatch.setattr(P, "generate_draft", boom)
    P.process_incoming(msg.id)
    assert db.session.get(Message, msg.id).category_id == cat.id


def test_a_shared_mailbox_stays_quiet_on_noise(app, owner, monkeypatch):
    """Geen Teams-melding voor een nieuwsbrief — anders is het kanaal onbruikbaar."""
    import app.tasks.pipeline as P
    import app.tasks.teams_notify as T

    acct = _account(owner, shared=True, email="support@acme-ruis.be")
    _enable(acct)
    _noise_category(owner.organization)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    monkeypatch.setattr(T, "notify_new_mail", lambda m: (_ for _ in ()).throw(
        AssertionError("ruis hoort niet in het kanaal")))
    P.process_incoming(msg.id)


# ── De andere kant: niets mag onterecht verdwijnen ───────────────────────────
def test_a_normal_customer_mail_goes_the_usual_way(app, owner):
    acct = _account(owner)
    _enable(acct)
    _noise_category(owner.organization)
    msg = _message(owner, acct, sender_email="jan@klant.be", subject="Offerte?",
                   body="Kan ik een offerte krijgen?")

    assert noise.classify_as_noise(db.session.get(Message, msg.id)) is None


def test_the_filter_is_off_by_default(app, owner):
    acct = _account(owner)
    _noise_category(owner.organization)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    assert noise.filter_enabled_for(msg) is False
    assert noise.classify_as_noise(msg) is None


def test_without_a_noise_category_nothing_is_filtered(app, owner):
    acct = _account(owner)
    _enable(acct)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    assert noise.classify_as_noise(msg) is None


def test_a_thread_you_already_worked_on_is_never_noise(app, owner):
    """Een bevestigingsmail in een lopend gesprek draagt soms een uitschrijflink. Zodra
    er iemand aan werkt, is het gesprek belangrijker dan de header."""
    from app.models.base import utcnow

    acct = _account(owner)
    _enable(acct)
    _noise_category(owner.organization)

    beantwoord = _message(owner, acct, unsubscribe_url="https://x/uit", replied_at=utcnow())
    toegewezen = _message(owner, acct, unsubscribe_url="https://x/uit", assignee_user_id=owner.id)

    assert noise.classify_as_noise(beantwoord) is None
    assert noise.classify_as_noise(toegewezen) is None


def test_noise_does_not_cross_organizations(app, owner):
    """De ruiscategorie van de buren is niet die van jou."""
    other = create_company("Concurrent BV", "O", "owner@concurrent-ruis.be", "geheim123").organization
    _noise_category(other)

    acct = _account(owner)
    _enable(acct)
    msg = _message(owner, acct, unsubscribe_url="https://x/uit")

    assert noise.classify_as_noise(msg) is None


# ── Beheer ───────────────────────────────────────────────────────────────────
def test_marking_a_category_as_noise_turns_drafting_off(client, app, owner):
    """Het scherm mag geen categorie kunnen opleveren die belooft concepten te maken
    én ruis te zijn."""
    client.post("/auth/login", data={"email": "owner@acme-ruis.be", "password": "geheim123"})
    cat = Category(organization_id=owner.organization_id, key="nieuws", name="Nieuwsbrieven",
                   ai="Mailings.", draft_enabled=True, position=50)
    db.session.add(cat)
    db.session.commit()

    r = client.put(f"/api/categories/{cat.id}", json={
        "name": "Nieuwsbrieven", "ai": "Mailings.", "draft_enabled": True, "is_noise": True,
    })
    assert r.get_json()["ok"]
    refreshed = db.session.get(Category, cat.id)
    assert refreshed.is_noise is True and refreshed.draft_enabled is False


def test_the_toggle_is_saved_from_the_settings_screen(client, app, owner):
    client.post("/auth/login", data={"email": "owner@acme-ruis.be", "password": "geheim123"})
    client.post("/app/settings/voorkeuren", data={"noise_filter_enabled": "on"}, follow_redirects=True)
    assert db.session.get(StyleProfile, owner.style_profile.id).noise_filter_enabled is True

    client.post("/app/settings/voorkeuren", data={}, follow_redirects=True)
    assert db.session.get(StyleProfile, owner.style_profile.id).noise_filter_enabled is False

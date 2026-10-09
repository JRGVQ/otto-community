"""Fase 4: bewerkbaar stijlprofiel + handtekeningen per verzenddomein.

Twee beloftes worden hier bewaakt:
1. wat de gebruiker zelf invult, wordt niet overschreven door de volgende leerronde —
   anders is "bewerkbaar" een loze belofte;
2. een concept krijgt de handtekening van het domein waaruit het vertrekt.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, Signature, StyleProfile
from app.tasks.draft import signature_for
from app.tasks.learn import learn_user_style, pin_style_fields


@pytest.fixture()
def owner(app):
    user = create_company("Acme BV", "Owner Name", "owner@acme-sig.be", "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    return user


@pytest.fixture()
def style(app, owner):
    sp = owner.style_profile or StyleProfile(user_id=owner.id)
    db.session.add(sp)
    db.session.commit()
    return sp


def _account(owner, email="owner@acme-sig.be", shared=False):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id, provider="outlook",
                       email=email, status="connected", mailbox_type="shared" if shared else "personal")
    db.session.add(acct)
    db.session.commit()
    return acct


def _sent_draft(owner, text):
    """Een verzonden concept — de bron waaruit learn_user_style leert."""
    from app.constants import DRAFT_SENT
    from app.models import Draft, Message

    msg = Message(organization_id=owner.organization_id, user_id=owner.id,
                  sender="Klant", sender_email="klant@voorbeeld.be", subject="Vraag")
    db.session.add(msg)
    db.session.flush()
    db.session.add(Draft(message_id=msg.id, status=DRAFT_SENT, current_text=text))
    db.session.commit()


# ── Bewerkbaar stijlprofiel ──────────────────────────────────────────────────
def test_learning_fills_the_profile(app, owner, style):
    _sent_draft(owner, "Beste klant\nDank voor je bericht, we kijken dit meteen na voor je.")
    assert learn_user_style(owner.id) is not None
    assert style.learned_at is not None


def test_pinned_greeting_survives_relearning(app, owner, style):
    """De kern: wat de gebruiker zelf zette, blijft staan."""
    style.greeting = "Dag"
    pin_style_fields(style, ["greeting"])
    db.session.commit()

    _sent_draft(owner, "Hallo daar\nBedankt voor je bericht, ik kijk het even na voor je.")
    learn_user_style(owner.id)
    assert style.greeting == "Dag"


def test_unpinned_greeting_is_relearned(app, owner, style):
    style.greeting = "Dag"
    db.session.commit()  # niet vastgezet
    _sent_draft(owner, "Hallo\nBedankt voor je bericht, ik kijk het even na voor je.")
    learn_user_style(owner.id)
    assert style.greeting == "Hallo"


def test_pinned_examples_survive_relearning(app, owner, style):
    style.raw_profile = {"examples": ["Mijn eigen zin die moet blijven staan."], "pinned": ["examples"]}
    db.session.commit()
    _sent_draft(owner, "Hallo\nDit is een geleerde zin die lang genoeg is om als voorbeeld te dienen.")
    learn_user_style(owner.id)
    assert style.raw_profile["examples"] == ["Mijn eigen zin die moet blijven staan."]


def test_saving_the_style_pins_it(app, client, owner):
    client.post("/auth/login", data={"email": "owner@acme-sig.be", "password": "geheim123"})
    client.post("/app/settings/stijl", data={
        "greeting": "Dag", "signoff": "Tot binnenkort", "examples": "Een eigen voorbeeldzin.",
    })
    sp = owner.style_profile
    assert sp.greeting == "Dag" and sp.signoff == "Tot binnenkort"
    assert set(sp.raw_profile["pinned"]) == {"greeting", "signoff", "examples"}


def test_clearing_a_field_hands_it_back_to_otto(app, client, owner, style):
    style.greeting = "Dag"
    pin_style_fields(style, ["greeting", "signoff", "examples"])
    db.session.commit()

    client.post("/auth/login", data={"email": "owner@acme-sig.be", "password": "geheim123"})
    client.post("/app/settings/stijl", data={"greeting": "", "signoff": "Groeten", "examples": ""})
    assert "greeting" not in style.raw_profile["pinned"]
    assert "examples" not in style.raw_profile["pinned"]
    assert "signoff" in style.raw_profile["pinned"]


def test_relearn_route_reports_when_there_is_nothing_to_learn(app, client, owner):
    client.post("/auth/login", data={"email": "owner@acme-sig.be", "password": "geheim123"})
    resp = client.post("/app/settings/stijl/leren", follow_redirects=True)
    assert "te weinig verzonden mail" in resp.get_data(as_text=True)


# ── Diep leren: uit de échte verzonden mail ──────────────────────────────────
class _FakeSentProvider:
    """Provider-fake die verzonden mail teruggeeft, zoals list_recent_sent doet."""

    def __init__(self, bodies):
        self.bodies = bodies

    def is_configured(self):
        return True

    def list_recent_sent(self, days=120, limit=40):
        return [{"body": b} for b in self.bodies]


def test_deep_learn_reads_the_sent_mailbox_and_writes_a_description(app, owner, style, monkeypatch):
    """De knop leest de Verzonden-items — niet enkel Otto's eigen concepten — en zet een
    leesbare stijlbeschrijving in het profiel."""
    _account(owner, "owner@acme-sig.be")  # verbonden, persoonlijk postvak
    provider = _FakeSentProvider([
        "Dag Peter\n\nBedankt voor je bericht, ik kijk het na en kom er snel op terug.\n\nGroetjes\nSam",
        "Dag Sofie\n\nTop, dat is in orde. Ik bevestig de afspraak.\n\nGroetjes\nSam",
    ])
    monkeypatch.setattr("app.providers.get_provider", lambda a: provider)

    sp = learn_user_style(owner.id, deep=True)
    assert sp is not None
    assert sp.raw_profile.get("description")           # het vak is niet meer leeg
    assert "Groetjes" in sp.raw_profile["description"]  # afsluiting echt uit de mail gehaald
    assert sp.greeting == "Dag"


def test_deep_learn_respects_a_pinned_greeting(app, owner, style, monkeypatch):
    style.greeting = "Beste"
    pin_style_fields(style, ["greeting"])
    db.session.commit()
    _account(owner, "owner@acme-sig.be")
    monkeypatch.setattr("app.providers.get_provider",
                        lambda a: _FakeSentProvider(["Dag\n\nEven kort, dank!\n\nGroetjes\nSam"]))
    learn_user_style(owner.id, deep=True)
    assert style.greeting == "Beste"  # wat de gebruiker zelf zette blijft staan


def test_auto_learn_on_send_makes_no_provider_call(app, owner, style, monkeypatch):
    """Het verzend-pad (deep=False) mag nooit de mailbox lezen of het AI-model aanroepen —
    dat zou geld en tijd kosten bij élke verzending."""
    _account(owner, "owner@acme-sig.be")

    def _boom(_):
        raise AssertionError("get_provider mag niet aangeroepen worden in het goedkope pad")

    monkeypatch.setattr("app.providers.get_provider", _boom)
    _sent_draft(owner, "Hallo\nBedankt voor je bericht, ik kijk het even na voor je.")
    assert learn_user_style(owner.id) is not None  # standaard = deep=False


# ── Geen dubbele slotgroet onder een HTML-handtekening ───────────────────────
def test_html_signature_greeting_is_stripped_to_avoid_doubling(app, owner):
    """Een HTML-handtekening die zelf met 'Met vriendelijke groeten,' opent, mag die groet
    niet meer bevatten: het concept schrijft de groet er al één keer boven."""
    from app.signatures import resolve_rich

    db.session.add(Signature(
        organization_id=owner.organization_id, domain="acme-sig.be", name="Acme", body="",
        html='<div style="margin-bottom:10px;">Met vriendelijke groeten,</div>'
             '<table><tr><td>Sam Verl</td></tr></table>'))
    db.session.commit()
    acct = _account(owner, "owner@acme-sig.be")
    resolved = resolve_rich(owner, account=acct)
    assert resolved.html is not None
    assert "vriendelijke groeten" not in resolved.html.lower()  # groet weg
    assert "Sam Verl" in resolved.html                         # de kaart blijft


def test_builtin_templates_carry_no_signoff(app):
    from app.signature_templates import builtins

    for t in builtins():
        assert "vriendelijke groeten" not in t["html"].lower(), t["key"]


# ── Handtekeningen ───────────────────────────────────────────────────────────
def test_org_domain_signature_is_used_for_that_domain(app, owner):
    db.session.add(Signature(organization_id=owner.organization_id, domain="acme-sig.be",
                             name="Acme", body="Met vriendelijke groeten,\nAcme BV"))
    db.session.commit()
    acct = _account(owner, "owner@acme-sig.be")
    assert signature_for(owner, account=acct) == "Met vriendelijke groeten,\nAcme BV"


def test_a_different_domain_does_not_pick_up_the_signature(app, owner, style):
    db.session.add(Signature(organization_id=owner.organization_id, domain="acme-sig.be", body="Acme BV"))
    style.signature = "Persoonlijk"
    db.session.commit()
    acct = _account(owner, "owner@ander-domein.be")
    assert signature_for(owner, account=acct) == "Persoonlijk"


def test_org_default_applies_when_no_domain_matches(app, owner, style):
    db.session.add(Signature(organization_id=owner.organization_id, domain=None, body="Standaard van de zaak"))
    style.signature = None  # de eigenaar krijgt er bij registratie standaard een
    db.session.commit()
    acct = _account(owner, "owner@ergens.be")
    assert signature_for(owner, account=acct) == "Standaard van de zaak"


def test_personal_signature_beats_the_org_default(app, owner, style):
    db.session.add(Signature(organization_id=owner.organization_id, domain=None, body="Standaard"))
    style.signature = "Mijn eigen"
    db.session.commit()
    acct = _account(owner, "owner@ergens.be")
    assert signature_for(owner, account=acct) == "Mijn eigen"


def test_shared_mailbox_signature_wins_over_everything(app, owner, style):
    from app.models import MailboxSettings

    db.session.add(Signature(organization_id=owner.organization_id, domain="acme-sig.be", body="Domein"))
    style.signature = "Persoonlijk"
    acct = _account(owner, "team@acme-sig.be", shared=True)
    mb = MailboxSettings(mail_account_id=acct.id, signature="Team Support")
    db.session.add(mb)
    db.session.commit()
    assert signature_for(owner, mb, account=acct) == "Team Support"


def test_fallback_is_never_a_placeholder(app, owner, style):
    style.signature = None
    db.session.commit()
    acct = _account(owner, "owner@ergens.be")
    assert signature_for(owner, account=acct) == "Groeten,\nOwner"


# ── Beheer-scherm ────────────────────────────────────────────────────────────
def _login(client):
    client.post("/auth/login", data={"email": "owner@acme-sig.be", "password": "geheim123"})
    return client


def test_admin_can_create_and_delete_a_signature(app, client, owner):
    c = _login(client)
    c.post("/admin/signatures/save", data={"name": "Acme", "domain": "ACME-SIG.BE", "body": "Groeten,\nAcme"})
    row = db.session.query(Signature).filter_by(organization_id=owner.organization_id).first()
    assert row is not None and row.domain == "acme-sig.be"  # genormaliseerd

    c.post(f"/admin/signatures/{row.id}/delete")
    assert db.session.query(Signature).filter_by(organization_id=owner.organization_id).count() == 0


def test_second_permanent_signature_on_same_domain_is_refused(app, client, owner):
    """Twee permanente handtekeningen op één domein mag niet: de tweede (zonder sig_id)
    wordt geweigerd met een conflict-melding. Bijwerken doe je door te bewerken (met sig_id),
    of door de campagne een tijdvenster te geven."""
    c = _login(client)
    c.post("/admin/signatures/save", data={"domain": "acme-sig.be", "body": "Eerste"})
    resp = c.post("/admin/signatures/save", data={"domain": "acme-sig.be", "body": "Tweede"},
                  follow_redirects=True)
    assert "al actief" in resp.get_data(as_text=True)
    rows = db.session.query(Signature).filter_by(organization_id=owner.organization_id).all()
    assert len(rows) == 1 and rows[0].body == "Eerste"  # de eerste blijft ongewijzigd


def test_invalid_domain_is_refused(app, client, owner):
    c = _login(client)
    resp = c.post("/admin/signatures/save", data={"domain": "geen domein", "body": "x"}, follow_redirects=True)
    assert "geen geldige domeinnaam" in resp.get_data(as_text=True)
    assert db.session.query(Signature).count() == 0


def test_empty_signature_is_refused(app, client, owner):
    c = _login(client)
    resp = c.post("/admin/signatures/save", data={"domain": "acme-sig.be", "body": "  "}, follow_redirects=True)
    assert "niet leeg" in resp.get_data(as_text=True)


def test_signature_screen_renders(app, client, owner):
    html = _login(client).get("/admin/signatures").get_data(as_text=True)
    assert "Handtekeningen" in html


def test_signatures_are_scoped_to_the_organisation(app, client, owner):
    other = create_company("Rivaal NV", "R", "r@rivaal-sig.be", "geheim123").organization
    sig = Signature(organization_id=other.id, domain="rivaal-sig.be", body="Van de rivaal")
    db.session.add(sig)
    db.session.commit()

    c = _login(client)
    assert c.post(f"/admin/signatures/{sig.id}/delete").status_code == 404


def test_deep_delete_purges_signatures(app, owner):
    from app.tenancy import delete_org_deep

    oid = owner.organization_id
    db.session.add(Signature(organization_id=oid, domain=None, body="x"))
    db.session.commit()
    delete_org_deep(owner.organization)
    db.session.commit()
    assert db.session.query(Signature).filter_by(organization_id=oid).count() == 0


def test_the_org_default_skips_a_disabled_signature(app, owner):
    """Regressie: de domeinloze terugval negeerde `enabled`/tijdvenster.

    Na "schakel de andere uit" bij een domeinconflict stonden er twee domeinloze rijen.
    `default_for` gaf de uitgeschakelde (laagste id) terug, `_usable` verwierp die, en er
    viel gééN org-handtekening meer onder de concepten."""
    from app.signatures import default_for

    oud = Signature(organization_id=owner.organization_id, domain=None,
                    name="Oud", body="Oude standaard", enabled=False)
    nieuw = Signature(organization_id=owner.organization_id, domain=None,
                      name="Nieuw", body="Nieuwe standaard", enabled=True)
    db.session.add_all([oud, nieuw])
    db.session.commit()

    gekozen = default_for(owner.organization)
    assert gekozen is not None
    assert gekozen.name == "Nieuw"   # niet de uitgeschakelde met het laagste id

"""Fase 3 — regel-gebaseerde handtekening-toewijzing (verzender/ontvanger-voorwaarden).

Bewaakte beloftes:
1. een regel wint van de vaste toewijzing/domein/standaard (specifieker);
2. alle voorwaarden van een regel moeten kloppen (AND); een regel zonder voorwaarden matcht nooit;
3. intern-vs-extern komt uit de geverifieerde domeinen; recipient-regels vereisen een bericht;
4. nul regels = exact het oude gedrag; een uitgeschakelde regel telt niet mee;
5. priority bepaalt de volgorde (laag wint); M365-groep-lidmaatschap matcht;
6. de beheerroutes: opslaan met voorwaarden, lege-voorwaarden geweigerd, delete org-gescoped.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import (
    DomainClaim,
    MailAccount,
    Message,
    Signature,
    SignatureAssignment,
    SignatureRule,
    SignatureRuleCondition,
    User,
)
from app.models.base import utcnow
from app.seed.demo_data import DEMO_PASSWORD
from app.signatures import resolve_rich


@pytest.fixture()
def owner(app):
    user = create_company("Regels BV", "Ruben Regel", "ruben@regels-x.be", "geheim123")
    user.email_verified = user.onboarded = True
    user.department = "Sales"
    # Wis de auto-aangemaakte persoonlijke stijl-handtekening: die wint anders van de
    # org-standaard (bestaand gedrag) en maskeert het terugval-pad in deze tests.
    if user.style_profile:
        user.style_profile.signature = None
    db.session.commit()
    return user


def _account(owner, groups=None):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="ruben@regels-x.be", status="connected",
                       mailbox_type="personal", graph_group_ids=groups or [])
    db.session.add(acct)
    db.session.commit()
    return acct


def _sig(owner, name, *, body="Groeten", html=None, domain=None):
    s = Signature(organization_id=owner.organization_id, domain=domain, name=name, body=body, html=html)
    db.session.add(s)
    db.session.commit()
    return s


def _rule(owner, sig, conds, *, priority=100, enabled=True, name="R"):
    r = SignatureRule(organization_id=owner.organization_id, signature_id=sig.id,
                      name=name, priority=priority, enabled=enabled)
    db.session.add(r)
    db.session.flush()
    for field, op, value in conds:
        db.session.add(SignatureRuleCondition(organization_id=owner.organization_id,
                                              rule_id=r.id, field=field, op=op, value=value))
    db.session.commit()
    return r


def _message(owner, acct, sender_email="klant@extern.com"):
    m = Message(organization_id=owner.organization_id, user_id=owner.id, account_id=acct.id,
                sender="Klant", sender_email=sender_email, subject="V",
                provider_message_id="PM", provider_thread_id="CT")
    db.session.add(m)
    db.session.commit()
    return m


def _verify_domain(owner, domain):
    db.session.add(DomainClaim(organization_id=owner.organization_id, domain=domain,
                               token="otto-verify-test", verified_at=utcnow()))
    db.session.commit()


# ── backward compat ──────────────────────────────────────────────────────────
def test_zero_rules_keeps_legacy_behaviour(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    # Geen regels → de org-standaard (oude pad).
    assert resolve_rich(owner, account=acct).name == "Standaard"


def test_rule_wins_over_assignment(app, owner):
    acct = _account(owner)
    assigned = _sig(owner, "Toegewezen")
    db.session.add(SignatureAssignment(organization_id=owner.organization_id,
                                       signature_id=assigned.id, kind="user", value=str(owner.id)))
    ruled = _sig(owner, "Via regel")
    _rule(owner, ruled, [("sender_department", "equals", "Sales")])
    db.session.commit()
    # De regel matcht (afdeling Sales) en wint van de expliciete toewijzing.
    assert resolve_rich(owner, account=acct).name == "Via regel"


# ── matching ─────────────────────────────────────────────────────────────────
def test_all_conditions_must_match(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    ruled = _sig(owner, "AND")
    _rule(owner, ruled, [("sender_department", "equals", "Sales"),
                         ("sender_mailbox", "equals", "iemand-anders@x.be")])
    # Tweede voorwaarde klopt niet → regel matcht niet → val terug op standaard.
    assert resolve_rich(owner, account=acct).name == "Standaard"


def test_rule_without_conditions_never_matches(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    empty = _sig(owner, "Leeg")
    r = SignatureRule(organization_id=owner.organization_id, signature_id=empty.id, name="leeg")
    db.session.add(r)
    db.session.commit()
    assert resolve_rich(owner, account=acct).name == "Standaard"


def test_recipient_scope_internal_vs_external(app, owner):
    acct = _account(owner)
    _verify_domain(owner, "regels-x.be")
    _sig(owner, "Standaard")
    extern = _sig(owner, "Extern-Engels")
    _rule(owner, extern, [("recipient_scope", "is", "external")])

    # Extern bericht → regel matcht.
    m_ext = _message(owner, acct, sender_email="klant@buiten.com")
    assert resolve_rich(owner, account=acct, message=m_ext).name == "Extern-Engels"
    # Intern bericht (eigen geverifieerd domein) → regel matcht niet → standaard.
    m_int = _message(owner, acct, sender_email="collega@regels-x.be")
    assert resolve_rich(owner, account=acct, message=m_int).name == "Standaard"


def test_recipient_rule_skipped_without_message(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    extern = _sig(owner, "Extern")
    _rule(owner, extern, [("recipient_scope", "is", "external")])
    # Add-in-pad: geen bericht → recipient-regel matcht niet → standaard.
    assert resolve_rich(owner, account=acct).name == "Standaard"


def test_priority_orders_rules(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    a = _sig(owner, "A")
    b = _sig(owner, "B")
    _rule(owner, a, [("sender_department", "equals", "Sales")], priority=50, name="A")
    _rule(owner, b, [("sender_department", "equals", "Sales")], priority=10, name="B")
    # Beide matchen; laagste priority (10 → B) wint.
    assert resolve_rich(owner, account=acct).name == "B"


def test_disabled_rule_is_ignored(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    off = _sig(owner, "Uit")
    _rule(owner, off, [("sender_department", "equals", "Sales")], enabled=False)
    assert resolve_rich(owner, account=acct).name == "Standaard"


def test_sender_group_membership(app, owner):
    acct = _account(owner, groups=["GID-SALES", "GID-ALL"])
    _sig(owner, "Standaard")
    grp = _sig(owner, "Salesgroep")
    _rule(owner, grp, [("sender_group", "in", "gid-sales,gid-mkt")])
    assert resolve_rich(owner, account=acct).name == "Salesgroep"


def test_contains_and_in_operators(app, owner):
    acct = _account(owner)
    _sig(owner, "Standaard")
    dom = _sig(owner, "DomeinMatch")
    m = _message(owner, acct, sender_email="jan@acme.co.uk")
    _rule(owner, dom, [("recipient_domain", "contains", "acme")])
    assert resolve_rich(owner, account=acct, message=m).name == "DomeinMatch"


# ── beheerroutes ─────────────────────────────────────────────────────────────
@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_rule_save_and_delete_routes(app, auth):
    user = _demo_user()
    sig = Signature(organization_id=user.organization_id, name="Std", body="x")
    db.session.add(sig)
    db.session.commit()

    r = auth.post("/admin/signatures/rules/save", data={
        "name": "Extern", "priority": "20", "enabled": "on", "signature_id": str(sig.id),
        "cond_field": ["recipient_scope", ""], "cond_op": ["is", "equals"],
        "cond_value": ["external", ""],
    }, follow_redirects=True)
    assert r.status_code == 200
    rule = db.session.query(SignatureRule).filter_by(organization_id=user.organization_id).one()
    assert rule.priority == 20 and rule.enabled
    assert len(rule.conditions) == 1 and rule.conditions[0].field == "recipient_scope"

    # Lege voorwaarden → geweigerd (geen regel bijgemaakt).
    auth.post("/admin/signatures/rules/save", data={
        "name": "Leeg", "signature_id": str(sig.id),
        "cond_field": [""], "cond_op": ["equals"], "cond_value": [""]}, follow_redirects=True)
    assert db.session.query(SignatureRule).count() == 1

    # Delete van een andere org → 404.
    other = create_company("X BV", "X Y", "x@x-r.be", "geheim123")
    osig = Signature(organization_id=other.organization_id, name="O", body="y")
    db.session.add(osig)
    db.session.flush()
    orule = SignatureRule(organization_id=other.organization_id, signature_id=osig.id, name="hun")
    db.session.add(orule)
    db.session.commit()
    assert auth.post(f"/admin/signatures/rules/{orule.id}/delete").status_code == 404
    assert auth.post(f"/admin/signatures/rules/{rule.id}/delete",
                     follow_redirects=True).status_code == 200
    assert db.session.get(SignatureRule, rule.id) is None

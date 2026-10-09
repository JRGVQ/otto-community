"""Per-handtekening-config: aan/uit, tijdvenster, ontvanger-scope, uitzonderingen + domein-conflict.

Bewaakte beloftes:
1. active_at = ingeschakeld én binnen het (optionele) tijdvenster;
2. een uitgeschakelde/verlopen handtekening wordt overgeslagen → de standaard neemt weer over;
3. een lopend tijdvenster (campagne) wint van de permanente standaard op hetzelfde domein;
4. domain_conflict: twee permanente op één domein = conflict; permanent + gevensterd = OK;
   twee gevensterde die overlappen = conflict; niet-overlappend = OK; uitgeschakeld botst nooit;
5. uitzonderingen en ontvanger-scope filteren de handtekening weg;
6. de save-route weigert een botsende handtekening met een foutmelding.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import DomainClaim, MailAccount, Message, Signature
from app.signatures import domain_conflict, for_domain, resolve_rich


@pytest.fixture()
def owner(app):
    user = create_company("Config BV", "Coen Config", "coen@config-x.be", "geheim123")
    user.email_verified = user.onboarded = True
    if user.style_profile:
        user.style_profile.signature = None
    db.session.commit()
    return user


def _account(owner, groups=None, email="coen@config-x.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected",
                       mailbox_type="personal", graph_group_ids=groups or [])
    db.session.add(acct)
    db.session.commit()
    return acct


def _sig(owner, name, *, domain=None, html="<b>{{naam}}</b>", enabled=True,
         starts_at=None, ends_at=None, recipient_scope="all", exclusions=None):
    s = Signature(organization_id=owner.organization_id, domain=domain, name=name,
                  body="Groeten", html=html, enabled=enabled, starts_at=starts_at,
                  ends_at=ends_at, recipient_scope=recipient_scope, exclusions=exclusions)
    db.session.add(s)
    db.session.commit()
    return s


NOW = datetime(2026, 6, 15, 12, 0)


# ── active_at ────────────────────────────────────────────────────────────────
def test_active_at_window_and_enabled(app, owner):
    s = _sig(owner, "campaign", starts_at=datetime(2026, 6, 1), ends_at=datetime(2026, 6, 30))
    assert s.active_at(NOW) is True
    assert s.active_at(datetime(2026, 7, 1)) is False      # na het venster
    assert s.active_at(datetime(2026, 5, 1)) is False      # vóór het venster
    s.enabled = False
    assert s.active_at(NOW) is False                       # uitgeschakeld


# ── resolver: overslaan → standaard neemt over ──────────────────────────────
def test_disabled_signature_falls_back_to_default(app, owner):
    _sig(owner, "Standaard", domain=None, html="<b>DEFAULT</b>")
    _sig(owner, "Merk", domain="config-x.be", html="<b>BRAND</b>", enabled=False)
    acct = _account(owner)
    out = resolve_rich(owner, account=acct).html
    assert "DEFAULT" in out and "BRAND" not in out


def test_expired_window_falls_back_to_default(app, owner):
    _sig(owner, "Standaard", domain=None, html="<b>DEFAULT</b>")
    _sig(owner, "Kerst", domain="config-x.be", html="<b>XMAS</b>",
         starts_at=datetime(2000, 12, 1), ends_at=datetime(2000, 12, 31))  # lang voorbij
    acct = _account(owner)
    out = resolve_rich(owner, account=acct).html
    assert "DEFAULT" in out and "XMAS" not in out


def test_active_campaign_overrides_permanent_on_same_domain(app, owner):
    _sig(owner, "Merk permanent", domain="config-x.be", html="<b>BRAND</b>")
    far = datetime.utcnow()
    _sig(owner, "Campagne", domain="config-x.be", html="<b>CAMPAIGN</b>",
         starts_at=far - timedelta(days=1), ends_at=far + timedelta(days=1))
    acct = _account(owner)
    out = resolve_rich(owner, account=acct).html
    assert "CAMPAIGN" in out and "BRAND" not in out       # lopende campagne wint


# ── for_domain kiest de nu-actieve ──────────────────────────────────────────
def test_for_domain_prefers_active_windowed(app, owner):
    _sig(owner, "perm", domain="config-x.be", html="<b>P</b>")
    now = datetime.utcnow()
    _sig(owner, "camp", domain="config-x.be", html="<b>C</b>",
         starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    picked = for_domain(owner.organization, "config-x.be")
    assert picked.name == "camp"


# ── domain_conflict ─────────────────────────────────────────────────────────
def test_two_permanent_same_domain_conflict(app, owner):
    a = _sig(owner, "A", domain="config-x.be")
    clash = domain_conflict(owner.organization, "config-x.be", None, None, enabled=True)
    assert clash is not None and clash.id == a.id


def test_permanent_plus_windowed_ok(app, owner):
    _sig(owner, "perm", domain="config-x.be")
    clash = domain_conflict(owner.organization, "config-x.be",
                            datetime(2026, 12, 1), datetime(2026, 12, 31), enabled=True)
    assert clash is None                                   # campagne naast de standaard mag


def test_two_windowed_overlap_conflict(app, owner):
    _sig(owner, "w1", domain="config-x.be", starts_at=datetime(2026, 6, 1), ends_at=datetime(2026, 6, 20))
    clash = domain_conflict(owner.organization, "config-x.be",
                            datetime(2026, 6, 15), datetime(2026, 6, 30), enabled=True)
    assert clash is not None                               # overlappende vensters


def test_two_windowed_no_overlap_ok(app, owner):
    _sig(owner, "w1", domain="config-x.be", starts_at=datetime(2026, 6, 1), ends_at=datetime(2026, 6, 10))
    clash = domain_conflict(owner.organization, "config-x.be",
                            datetime(2026, 6, 11), datetime(2026, 6, 20), enabled=True)
    assert clash is None


def test_disabled_never_conflicts(app, owner):
    _sig(owner, "perm", domain="config-x.be")
    # nieuwe is uitgeschakeld → geen conflict, ook al is er een permanente
    assert domain_conflict(owner.organization, "config-x.be", None, None, enabled=False) is None


def test_conflict_excludes_self(app, owner):
    a = _sig(owner, "A", domain="config-x.be")
    assert domain_conflict(owner.organization, "config-x.be", None, None,
                           enabled=True, exclude_id=a.id) is None


# ── uitzonderingen + ontvanger-scope ────────────────────────────────────────
def test_exclusion_skips_signature(app, owner):
    _sig(owner, "Standaard", domain=None, html="<b>DEFAULT</b>")
    _sig(owner, "Merk", domain="config-x.be", html="<b>BRAND</b>",
         exclusions={"users": [owner.id]})
    acct = _account(owner)
    out = resolve_rich(owner, account=acct).html
    assert "DEFAULT" in out and "BRAND" not in out         # afzender uitgezonderd


def test_recipient_scope_internal_skips_external(app, owner):
    db.session.add(DomainClaim(organization_id=owner.organization_id, domain="config-x.be",
                               token="tok", verified_at=datetime.utcnow()))
    _sig(owner, "Standaard", domain=None, html="<b>DEFAULT</b>")
    _sig(owner, "Intern", domain="config-x.be", html="<b>INTERN</b>", recipient_scope="internal")
    db.session.commit()
    acct = _account(owner)
    ext = Message(organization_id=owner.organization_id, account_id=acct.id,
                  provider_message_id="m1", sender_email="klant@buiten.be", subject="hoi")
    db.session.add(ext)
    db.session.commit()
    out = resolve_rich(owner, account=acct, message=ext).html
    assert "DEFAULT" in out and "INTERN" not in out        # externe ontvanger → interne sig overgeslagen


# ── save-route weigert een conflict ─────────────────────────────────────────
def test_save_route_rejects_domain_conflict(app, client, owner):
    _sig(owner, "Bestaand", domain="config-x.be")
    with client.session_transaction() as s:
        s["_user_id"] = str(owner.id)
    r = client.post("/admin/signatures/save", data={
        "name": "Tweede", "domain": "config-x.be", "html": "<b>x</b>", "enabled": "on",
    }, follow_redirects=True)
    assert r.status_code == 200
    # er is geen tweede permanente handtekening bijgekomen
    n = db.session.query(Signature).filter_by(organization_id=owner.organization_id, domain="config-x.be").count()
    assert n == 1

"""Org-events: de zakelijke tijdlijn per omgeving.

Kernbelofte die hier bewaakt wordt: dit is de enige cross-tenant weergave die de
platform-eigenaar heeft, dus ze mag *nooit* mailboxinhoud bevatten. Vandaar de
gecontroleerde woordenschat — een onbekende sleutel wordt geweigerd in plaats van
als vrije tekst weggeschreven.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.events import EVENT_LABELS, events_for, label_for, recent_events, record_event
from app.extensions import db
from app.models import OrgEvent
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def org(app):
    user = create_company("Acme BV", "Owner", "owner@acme-ev.be", "geheim123")
    user.email_verified = True  # zodat de eigenaar kan inloggen…
    user.onboarded = True       # …en niet eerst door de onboarding moet
    db.session.commit()
    return user.organization


# ── Recorder ─────────────────────────────────────────────────────────────────
def test_registration_records_creation(app, org):
    events = {e.event for e in events_for(org)}
    assert "org.created" in events


def test_record_event_refuses_unknown_keys(app, org):
    before = len(events_for(org))
    assert record_event(org, "iets.verzonnen", detail="mailboxinhoud") is None
    db.session.commit()
    assert len(events_for(org)) == before  # niets weggeschreven


def test_record_event_ignores_a_missing_org(app):
    assert record_event(None, "org.created") is None


def test_record_event_truncates_long_values(app, org):
    record_event(org, "license.status_changed", detail="x" * 400, actor="y" * 200)
    db.session.commit()
    row = events_for(org)[0]
    assert len(row.detail) == 255 and len(row.actor) == 64


def test_event_joins_the_callers_transaction(app, org):
    """Een gebeurtenis voor een teruggedraaide actie mag niet blijven staan."""
    record_event(org, "license.status_changed", detail="active → suspended")
    db.session.rollback()
    assert not any(e.event == "license.status_changed" for e in events_for(org))


def test_every_catalogue_key_has_a_label():
    assert all(EVENT_LABELS[k] and label_for(k) == EVENT_LABELS[k] for k in EVENT_LABELS)


# ── Emissiepunten ────────────────────────────────────────────────────────────
def test_owner_status_change_records_only_what_moved(app, client, org):
    _owner_client(client)

    client.post(f"/owner/companies/{org.id}/status", data={"action": "activate"})  # al actief
    assert "license.status_changed" not in {e.event for e in events_for(org)}  # geen ruis

    client.post(f"/owner/companies/{org.id}/status", data={"action": "suspend"})
    events = {e.event: e.detail for e in events_for(org)}
    assert events.get("license.status_changed") == "active → suspended"


def test_self_service_delete_records_the_event(app, client, org):
    client.post("/auth/login", data={"email": "owner@acme-ev.be", "password": "geheim123"})
    client.post("/app/account/delete", data={"confirm_name": org.name, "password": "geheim123"})

    deleted = [e for e in events_for(org) if e.event == "org.deleted"]
    assert deleted and "eigenaar zelf" in deleted[0].detail


def test_domain_verification_records_the_transition_once(app, org, monkeypatch):
    from app import domains
    from app.domains import claim_domain, verify_claim

    claim, _e = claim_domain(org, "acme.be")
    monkeypatch.setattr(domains, "resolve_txt", lambda name: [claim.record_value])

    assert verify_claim(claim) is True
    assert verify_claim(claim) is True  # dagelijkse hercontrole
    verified = [e for e in events_for(org) if e.event == "domain.verified"]
    assert len(verified) == 1  # één gebeurtenis, geen dagelijkse ruis

    monkeypatch.setattr(domains, "resolve_txt", lambda name: [])
    verify_claim(claim)
    assert [e for e in events_for(org) if e.event == "domain.revoked"]


# ── Filters ──────────────────────────────────────────────────────────────────
def test_recent_events_filters_by_group_and_org(app, org):
    other = create_company("Andere NV", "Owner", "owner@andere-ev.be", "geheim123").organization

    lifecycle = recent_events(group="lifecycle")
    assert lifecycle and all(e.event.startswith("org.") for e in lifecycle)

    scoped = recent_events(org_id=org.id)
    assert scoped and all(e.organization_id == org.id for e in scoped)
    assert any(e.organization_id == other.id for e in recent_events())


def test_recent_events_ignores_an_unknown_group(app, org):
    assert len(recent_events(group="verzonnen")) == len(recent_events())


# ── Backoffice-scherm ────────────────────────────────────────────────────────
def _owner_client(client):
    """De geseede demo-gebruiker is in testconfig al platform-eigenaar."""
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def test_events_screen_renders_for_the_platform_owner(app, client, org):
    c = _owner_client(client)
    resp = c.get("/owner/events")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Gebeurtenissen" in html and "Omgeving aangemaakt" in html


def test_events_screen_is_closed_to_ordinary_users(app, client, org):
    client.post("/auth/login", data={"email": "owner@acme-ev.be", "password": "geheim123"})
    assert client.get("/owner/events").status_code == 403


def test_events_screen_filters_work(app, client, org):
    c = _owner_client(client)
    assert c.get("/owner/events?group=license").status_code == 200
    assert c.get(f"/owner/events?org={org.id}").status_code == 200


def test_company_detail_shows_the_timeline(app, client, org):
    c = _owner_client(client)
    html = c.get(f"/owner/companies/{org.id}").get_data(as_text=True)
    assert "Omgeving aangemaakt" in html


# ── Opruimen ─────────────────────────────────────────────────────────────────
def test_deep_delete_purges_events(app, org):
    """org_events heeft een non-nullable FK: zonder expliciete purge faalt de wis."""
    from app.tenancy import delete_org_deep

    oid = org.id
    assert db.session.query(OrgEvent).filter_by(organization_id=oid).count() > 0
    delete_org_deep(org)
    db.session.commit()
    assert db.session.query(OrgEvent).filter_by(organization_id=oid).count() == 0


def test_deep_delete_purges_domain_claims(app, org):
    from app.domains import claim_domain
    from app.models import DomainClaim
    from app.tenancy import delete_org_deep

    oid = org.id
    claim_domain(org, "acme.be")
    delete_org_deep(org)
    db.session.commit()
    assert db.session.query(DomainClaim).filter_by(organization_id=oid).count() == 0

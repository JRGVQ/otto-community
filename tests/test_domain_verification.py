"""Domeinverificatie via DNS-TXT: normalisatie, claim, verificatie en afdwinging.

DNS wordt gemockt — de tests raken nooit een echte resolver, en dekken expliciet ook
het geval waarin er géén resolver geïnstalleerd is (dan moet een check *closed* falen).
"""
from __future__ import annotations

import pytest

from app import domains
from app.auth.service import create_company
from app.domains import (
    DnsUnavailable,
    claim_domain,
    normalize_domain,
    registration_blocked_by,
    verified_owner,
    verify_claim,
)
from app.extensions import db


@pytest.fixture()
def org(app):
    return create_company("Acme BV", "Owner", "owner@acme-dom.be", "geheim123").organization


@pytest.fixture()
def other_org(app):
    return create_company("Rivaal NV", "Owner", "owner@rivaal-dom.be", "geheim123").organization


def _txt(monkeypatch, mapping):
    """Stub the resolver: {name: [values]}; a missing name resolves to no records."""
    monkeypatch.setattr(domains, "resolve_txt", lambda name: list(mapping.get(name, [])))


def _txt_unavailable(monkeypatch):
    def boom(name):
        raise DnsUnavailable("geen resolver")

    monkeypatch.setattr(domains, "resolve_txt", boom)


# ── Normalisatie ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("acme.be", "acme.be"),
    ("  ACME.BE  ", "acme.be"),
    ("https://www.acme.be/contact", "acme.be"),
    ("www.acme.be.", "acme.be"),
    ("iemand@acme.be", "acme.be"),
    ("sub.acme.co.uk", "sub.acme.co.uk"),
])
def test_normalize_domain_accepts(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "localhost", "geen domein", "acme", ".be", "a.b"])
def test_normalize_domain_rejects(raw):
    assert normalize_domain(raw) is None


# ── Claim ────────────────────────────────────────────────────────────────────
def test_claim_creates_pending_claim_with_record(app, org):
    claim, error = claim_domain(org, "acme.be")
    assert error is None
    assert claim.domain == "acme.be"
    assert not claim.is_verified
    assert claim.record_name == "_otto-verify.acme.be"
    assert claim.record_value.startswith("otto-verify=")
    assert claim.token in claim.record_value


def test_claim_is_idempotent(app, org):
    first, _ = claim_domain(org, "acme.be")
    again, error = claim_domain(org, "ACME.BE")
    assert error is None
    assert again.id == first.id  # geen tweede claim, en het token blijft gelijk


def test_claim_rejects_public_mail_providers(app, org):
    claim, error = claim_domain(org, "gmail.com")
    assert claim is None and "Publieke" in error


def test_claim_rejects_invalid_domain(app, org):
    claim, error = claim_domain(org, "geen-domein")
    assert claim is None and error


# ── Verificatie ──────────────────────────────────────────────────────────────
def test_verify_succeeds_on_matching_txt(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    assert verify_claim(claim) is True
    assert claim.is_verified and claim.last_error is None


def test_verify_accepts_bare_token(app, org, monkeypatch):
    """Klanten plakken vaak enkel de tokenwaarde in plaats van de volledige string."""
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.token]})
    assert verify_claim(claim) is True


def test_verify_fails_without_record(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {})
    assert verify_claim(claim) is False
    assert not claim.is_verified and "TXT-record niet gevonden" in claim.last_error


def test_verify_fails_on_wrong_token(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": ["otto-verify=iemand-anders-zijn-token"]})
    assert verify_claim(claim) is False


def test_verify_fails_closed_without_resolver(app, org, monkeypatch):
    """Zonder DNS-resolver mag een domein NOOIT als geverifieerd gelden."""
    claim, _e = claim_domain(org, "acme.be")
    _txt_unavailable(monkeypatch)
    assert verify_claim(claim) is False
    assert not claim.is_verified and "DNS" in claim.last_error


def test_verified_domain_is_revoked_when_record_disappears(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    assert verify_claim(claim) is True
    _txt(monkeypatch, {})  # klant haalt het record weg
    assert verify_claim(claim) is False
    assert verified_owner("acme.be") is None


def test_dns_outage_does_not_revoke_a_verified_domain(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    assert verify_claim(claim) is True
    _txt_unavailable(monkeypatch)
    verify_claim(claim)
    assert claim.is_verified  # onbereikbare DNS is geen bewijs van verlies


# ── Exclusiviteit ────────────────────────────────────────────────────────────
def test_second_org_cannot_claim_a_verified_domain(app, org, other_org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    stolen, error = claim_domain(other_org, "acme.be")
    assert stolen is None and "andere organisatie" in error


def test_pending_claim_loses_to_the_org_that_verified_first(app, org, other_org, monkeypatch):
    mine, _a = claim_domain(org, "acme.be")
    theirs, _b = claim_domain(other_org, "acme.be")  # beide claims mogen naast elkaar bestaan
    assert theirs is not None

    _txt(monkeypatch, {"_otto-verify.acme.be": [mine.record_value]})
    assert verify_claim(mine) is True

    _txt(monkeypatch, {"_otto-verify.acme.be": [theirs.record_value]})
    assert verify_claim(theirs) is False  # te laat: het domein is al vergeven
    assert verified_owner("acme.be").id == org.id


def test_verified_owner_ignores_trashed_orgs(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    from app.tenancy import soft_delete_org

    soft_delete_org(org)
    assert verified_owner("acme.be") is None  # een omgeving in de prullenbak claimt niets


# ── Afdwinging: registratie ──────────────────────────────────────────────────
def test_registration_blocked_on_verified_domain(app, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    blocker = registration_blocked_by("nieuw@acme.be")
    assert blocker is not None and blocker.id == org.id
    assert registration_blocked_by("nieuw@ergens-anders.be") is None


def test_register_route_refuses_a_verified_domain(app, client, org, monkeypatch):
    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    resp = client.post("/auth/register", data={
        "company": "Namaak BV", "name": "Indringer", "email": "indringer@acme.be",
        "password": "geheim123", "confirm": "geheim123",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.models import User

    assert db.session.query(User).filter_by(email="indringer@acme.be").first() is None


# ── Afdwinging: SSO ──────────────────────────────────────────────────────────
def _enable_sso(o, *, domains_list):
    o.sso.enabled = True
    o.sso.oidc_issuer = "https://idp.example/v2.0"
    o.sso.oidc_client_id = "client-123"
    o.sso.allowed_domains = domains_list
    db.session.commit()


def test_sso_routes_unverified_domains_as_before(app, org):
    from app.auth.oauth.oidc import sso_org_for_email

    _enable_sso(org, domains_list=["acme.be"])
    assert sso_org_for_email("iemand@acme.be").id == org.id


def test_sso_cannot_be_hijacked_by_listing_someone_elses_verified_domain(app, org, other_org, monkeypatch):
    """De kern: een geverifieerd domein stuurt SSO enkel naar de eigenaar."""
    from app.auth.oauth.oidc import sso_org_for_email

    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    _enable_sso(org, domains_list=["acme.be"])
    _enable_sso(other_org, domains_list=["acme.be"])  # rivaal claimt hetzelfde domein

    assert sso_org_for_email("iemand@acme.be").id == org.id


def test_sso_returns_none_when_owner_has_no_usable_idp(app, org, other_org, monkeypatch):
    from app.auth.oauth.oidc import sso_org_for_email

    claim, _e = claim_domain(org, "acme.be")
    _txt(monkeypatch, {"_otto-verify.acme.be": [claim.record_value]})
    verify_claim(claim)

    _enable_sso(other_org, domains_list=["acme.be"])  # enkel de rivaal heeft SSO ingesteld
    assert sso_org_for_email("iemand@acme.be") is None

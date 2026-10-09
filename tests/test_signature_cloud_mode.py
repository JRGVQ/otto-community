"""Fase 5 — cloud-modus (Exchange transport-regel) voor handtekeningen.

Bewaakte beloftes:
1. de generator vertaalt placeholders naar Exchange-tokens, laat onmapbare vervallen, en
   levert een PowerShell-script met de marker-uitzondering;
2. de write-back is modus-bewust: client = handtekening in het concept (geen marker),
   cloud = niets in het concept (transport-regel doet het), combo = handtekening + marker;
3. de beheerroutes (modus opslaan + attesteren) staan achter manageProviders en een
   wijziging van de regel-definitie wist de attestatie.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import CloudSignatureSetting, MailAccount, Message, Signature, User
from app.seed.demo_data import DEMO_PASSWORD


# ── generator ────────────────────────────────────────────────────────────────
def test_disclaimer_maps_and_drops_placeholders():
    from app.cloud_signature import disclaimer_html, unmappable_placeholders

    html = "<div>{{naam}} — {{functie}} · {{linkedin}} · {{foto}}</div>"
    out = disclaimer_html(html)
    assert "%%DisplayName%%" in out and "%%Title%%" in out
    assert "{{linkedin}}" not in out and "{{foto}}" not in out  # onmapbaar → verdwenen
    assert set(unmappable_placeholders(html)) == {"linkedin", "foto"}


def test_powershell_script_has_rule_and_marker():
    from app.cloud_signature import DEFAULT_MARKER, powershell_script

    script = powershell_script("Otto cloud-handtekening", "<b>%%DisplayName%%</b>", DEFAULT_MARKER)
    assert "New-TransportRule" in script
    assert "-ApplyHtmlDisclaimerText" in script
    assert f"ExceptIfSubjectOrBodyContainsWords '{DEFAULT_MARKER}'" in script
    assert "%%DisplayName%%" in script


def test_meeting_script_scopes_to_calendaring():
    from app.cloud_signature import DEFAULT_MARKER, meeting_powershell_script, powershell_script

    script = meeting_powershell_script("Otto vergader-handtekening", "<b>%%DisplayName%%</b>")
    assert "New-TransportRule" in script
    assert "-MessageTypeMatches Calendaring" in script          # alleen vergader-/Teams-uitnodigingen
    assert f"ExceptIfSubjectOrBodyContainsWords '{DEFAULT_MARKER}'" in script
    # De gewone regel bevat géén MessageTypeMatches (treft alle mail).
    assert "-MessageTypeMatches" not in powershell_script("R", "<b>x</b>")


def test_marker_html_is_hidden():
    from app.cloud_signature import otto_marker_html

    m = otto_marker_html("otto-sig-v1")
    assert "otto-sig-v1" in m and "display:none" in m


def test_powershell_escapes_single_quotes():
    from app.cloud_signature import powershell_script

    script = powershell_script("R", "<b>D'Hondt</b>")
    assert "D''Hondt" in script  # PowerShell verdubbelt de apostrof


# ── write-back modus ─────────────────────────────────────────────────────────
@pytest.fixture()
def org_with_sig(app):
    user = create_company("Cloud BV", "Cara Cloud", "cara@cloud-x.be", "geheim123")
    user.email_verified = user.onboarded = True
    if user.style_profile:
        user.style_profile.signature = None
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="cara@cloud-x.be", status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.add(Signature(organization_id=user.organization_id, domain=None, name="Std",
                             body="Groeten", html="<b>{{naam}}</b>"))
    db.session.flush()
    msg = Message(organization_id=user.organization_id, user_id=user.id, account_id=acct.id,
                  sender="K", sender_email="k@x.be", subject="V", provider_message_id="PM", provider_thread_id="CT")
    db.session.add(msg)
    db.session.commit()
    return user, msg


def _set_mode(user, mode):
    db.session.add(CloudSignatureSetting(organization_id=user.organization_id, mode=mode))
    db.session.commit()


def test_client_mode_injects_without_marker(app, org_with_sig):
    from app.tasks.writeback import _org_signature

    _user, msg = org_with_sig  # geen instelling → client (standaard)
    html, text = _org_signature(msg)
    assert html and "Cara" in html and "otto-sig-v1" not in html


def test_cloud_mode_injects_nothing(app, org_with_sig):
    from app.tasks.writeback import _org_signature

    user, msg = org_with_sig
    _set_mode(user, "cloud")
    assert _org_signature(msg) == (None, None)  # transport-regel doet het server-side


def test_combo_mode_adds_marker(app, org_with_sig):
    from app.tasks.writeback import _org_signature

    user, msg = org_with_sig
    _set_mode(user, "combo")
    html, _text = _org_signature(msg)
    assert html and "Cara" in html and "otto-sig-v1" in html  # marker erbij


# ── beheerroutes ─────────────────────────────────────────────────────────────
@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


def test_cloud_save_and_attest_routes(app, auth):
    user = _demo_user()
    sig = Signature(organization_id=user.organization_id, name="Std", body="x", html="<b>{{naam}}</b>")
    db.session.add(sig)
    db.session.commit()

    r = auth.post("/admin/signatures/cloud/save", data={"mode": "combo", "signature_id": str(sig.id)},
                  follow_redirects=True)
    assert r.status_code == 200
    row = db.session.query(CloudSignatureSetting).filter_by(organization_id=user.organization_id).one()
    assert row.mode == "combo" and row.signature_id == sig.id and row.rule_attested_at is None

    # Attesteren zet de tijdstempel.
    auth.post("/admin/signatures/cloud/attest", follow_redirects=True)
    db.session.refresh(row)
    assert row.rule_attested_at is not None

    # Modus wijzigen wist de attestatie weer (regel-definitie veranderde).
    auth.post("/admin/signatures/cloud/save", data={"mode": "cloud", "signature_id": str(sig.id)},
              follow_redirects=True)
    db.session.refresh(row)
    assert row.mode == "cloud" and row.rule_attested_at is None


def test_cloud_card_renders_script(app, auth):
    user = _demo_user()
    db.session.add(Signature(organization_id=user.organization_id, domain=None, name="Std",
                             body="x", html="<b>{{naam}}</b> — {{functie}}"))
    db.session.commit()
    auth.post("/admin/signatures/cloud/save", data={"mode": "cloud"}, follow_redirects=True)
    page = auth.get("/admin/signatures").get_data(as_text=True)
    assert "New-TransportRule" in page and "Afleveringsmodus" in page

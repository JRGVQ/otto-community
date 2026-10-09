"""Fase 4 — afwezigheidsassistent (OOO) + geplande handtekening-banners.

Bewaakte beloftes:
1. graph_payload mapt status/audience/berichten/planning naar de juiste Graph-vorm;
2. push_pending_auto_replies duwt 'pending' rijen naar de provider en zet ze op 'synced'
   (of 'error' bij een provider zonder OOO-ondersteuning);
3. de OOO-route is org/gebruiker-gescoped (alleen eigen Outlook-postvak) en zet push_state
   terug op 'pending';
4. active_banner_html kiest de actieve banner (venster + hoogste weging); resolve_rich
   splitst hem in de HTML maar laat de platte tekst ongemoeid;
5. de banner-routes valideren en zijn org-gescoped.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import AutoReplySetting, MailAccount, Signature, SignatureBanner, User
from app.models.base import utcnow
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def owner(app):
    user = create_company("OOO BV", "Ola Out", "ola@ooo-x.be", "geheim123")
    user.email_verified = user.onboarded = True
    if user.style_profile:
        user.style_profile.signature = None
    db.session.commit()
    return user


def _account(owner, email="ola@ooo-x.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    return acct


@pytest.fixture()
def auth(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


def _demo_user():
    return db.session.query(User).filter_by(email="demo@example.com").first()


# ── auto-replies ─────────────────────────────────────────────────────────────
def test_graph_payload_maps_fields():
    from app.tasks.autoreply import graph_payload

    start, end = utcnow(), utcnow() + timedelta(days=3)
    s = AutoReplySetting(status="scheduled", external_audience="contactsOnly",
                         internal_reply_html="<b>intern</b>", external_reply_html="<b>extern</b>",
                         scheduled_start=start, scheduled_end=end)
    p = graph_payload(s)
    assert p["status"] == "scheduled" and p["externalAudience"] == "contactsOnly"
    assert p["internalReplyMessage"] == "<b>intern</b>" and p["externalReplyMessage"] == "<b>extern</b>"
    assert p["scheduledStartDateTime"]["timeZone"] == "UTC"
    # 'always' → alwaysEnabled; geen planning meegestuurd.
    s2 = AutoReplySetting(status="always")
    p2 = graph_payload(s2)
    assert p2["status"] == "alwaysEnabled" and "scheduledStartDateTime" not in p2


def test_push_pending_syncs_and_records(app, owner, monkeypatch):
    from app.tasks import autoreply as ar

    acct = _account(owner)
    s = AutoReplySetting(organization_id=owner.organization_id, mail_account_id=acct.id,
                         status="always", internal_reply_html="weg", push_state="pending")
    db.session.add(s)
    db.session.commit()

    seen = {}

    class FakeProvider:
        def is_configured(self):
            return True

        def set_automatic_replies(self, setting):
            seen["setting"] = setting

    monkeypatch.setattr(ar, "get_provider", lambda a: FakeProvider())
    assert ar.push_pending_auto_replies() == 1
    db.session.refresh(s)
    assert s.push_state == "synced" and s.last_pushed_at is not None
    assert seen["setting"]["status"] == "alwaysEnabled"
    # Tweede run doet niets meer (idempotent).
    assert ar.push_pending_auto_replies() == 0


def test_push_marks_error_without_provider_support(app, owner, monkeypatch):
    from app.tasks import autoreply as ar

    acct = _account(owner)
    s = AutoReplySetting(organization_id=owner.organization_id, mail_account_id=acct.id,
                         status="always", push_state="pending")
    db.session.add(s)
    db.session.commit()

    class NoOOO:
        def is_configured(self):
            return True

    monkeypatch.setattr(ar, "get_provider", lambda a: NoOOO())
    ar.push_pending_auto_replies()
    db.session.refresh(s)
    assert s.push_state == "error" and "Outlook" in (s.last_error or "")


def test_autoreply_route_is_scoped(app, auth):
    user = _demo_user()
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="demo@example.com", status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    r = auth.post("/admin/autoreplies/save", data={
        "account_id": str(acct.id), "status": "always", "external_audience": "all",
        "internal_reply_html": "Ik ben weg"}, follow_redirects=True)
    assert r.status_code == 200
    s = db.session.query(AutoReplySetting).filter_by(mail_account_id=acct.id).one()
    assert s.status == "always" and s.push_state == "pending"

    # Andermans postvak → 404.
    other = create_company("Ander BV", "A B", "a@ander-ooo.be", "geheim123")
    oacct = MailAccount(user_id=other.id, organization_id=other.organization_id,
                        provider="outlook", email="a@ander-ooo.be", status="connected", mailbox_type="personal")
    db.session.add(oacct)
    db.session.commit()
    assert auth.post("/admin/autoreplies/save", data={"account_id": str(oacct.id), "status": "always"}).status_code == 404


# ── banners ──────────────────────────────────────────────────────────────────
def _banner(owner, **kw):
    b = SignatureBanner(organization_id=owner.organization_id,
                        image_url=kw.pop("image_url", "https://acme.be/b.png"), **kw)
    db.session.add(b)
    db.session.commit()
    return b


def test_active_banner_picks_window_and_weight(app, owner):
    from app.signatures import active_banner_html

    now = utcnow()
    _banner(owner, name="oud", enabled=True, ends_at=now - timedelta(days=1))          # verlopen
    _banner(owner, name="uit", enabled=False)                                          # uit
    _banner(owner, name="laag", enabled=True, weight=1)
    _banner(owner, name="hoog", enabled=True, weight=5, link_url="https://x.be")
    res = active_banner_html(owner.organization, now)
    assert res is not None
    html, placement = res
    assert "https://x.be" in html and placement == "below"  # hoogste weging wint


def test_banner_spliced_into_html_not_text(app, owner):
    from app.signatures import resolve_rich

    acct = _account(owner)
    db.session.add(Signature(organization_id=owner.organization_id, domain=None, name="Std",
                             body="Groeten", html="<b>{{naam}}</b>"))
    _banner(owner, name="camp", enabled=True, image_url="https://acme.be/camp.png", placement="below")
    db.session.commit()
    rich = resolve_rich(owner, account=acct)
    assert rich.html and "https://acme.be/camp.png" in rich.html
    assert "camp.png" not in rich.text  # platte tekst blijft ongemoeid


def test_banner_above_placement(app, owner):
    from app.signatures import resolve_rich

    acct = _account(owner)
    db.session.add(Signature(organization_id=owner.organization_id, domain=None, name="Std",
                             body="Groeten", html="<b>SIG</b>"))
    _banner(owner, name="top", enabled=True, image_url="https://acme.be/top.png", placement="above")
    db.session.commit()
    html = resolve_rich(owner, account=acct).html
    assert html.index("top.png") < html.index("SIG")  # boven de handtekening


def test_banner_routes(app, auth):
    user = _demo_user()
    r = auth.post("/admin/signatures/banners/save", data={
        "name": "Kerst", "image_url": "https://acme.be/kerst.png", "placement": "above",
        "weight": "3", "enabled": "on"}, follow_redirects=True)
    assert r.status_code == 200
    b = db.session.query(SignatureBanner).filter_by(organization_id=user.organization_id).one()
    assert b.placement == "above" and b.weight == 3 and b.enabled

    # Zonder afbeelding-URL → geweigerd.
    auth.post("/admin/signatures/banners/save", data={"name": "leeg"}, follow_redirects=True)
    assert db.session.query(SignatureBanner).count() == 1

    # Delete van een andere org → 404.
    other = create_company("X BV", "X Y", "x@x-ban.be", "geheim123")
    ob = SignatureBanner(organization_id=other.organization_id, image_url="https://x.be/x.png")
    db.session.add(ob)
    db.session.commit()
    assert auth.post(f"/admin/signatures/banners/{ob.id}/delete").status_code == 404
    assert auth.post(f"/admin/signatures/banners/{b.id}/delete", follow_redirects=True).status_code == 200
    assert db.session.get(SignatureBanner, b.id) is None


def test_autoreplies_screen_renders(app, auth):
    page = auth.get("/admin/autoreplies").get_data(as_text=True)
    assert "Afwezigheidsassistent" in page

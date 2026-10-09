"""Banner-analytics: opt-in meting (weergaven + clicks), aggregaat, publieke endpoints.

Geen persoonsgegevens: alleen tellers per banner/dag.
"""
from __future__ import annotations

import urllib.parse

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import BannerStat, MailAccount, Signature, SignatureBanner


@pytest.fixture()
def owner(app):
    user = create_company("Meting BV", "Meia Meet", "meia@meet-x.be", "geheim123")
    user.email_verified = user.onboarded = True
    if user.style_profile:
        user.style_profile.signature = None
    db.session.commit()
    return user


def _banner(owner, track=False, link="https://acme.be/promo"):
    b = SignatureBanner(organization_id=owner.organization_id, name="camp",
                        image_url="https://x/c.png", link_url=link, enabled=True, track=track)
    db.session.add(b)
    db.session.commit()
    return b


# ── token + tellers ──────────────────────────────────────────────────────────
def test_token_roundtrip(app):
    from app.signature_analytics import banner_token, read_banner_token

    assert read_banner_token(banner_token(42)) == 42
    assert read_banner_token("onzin") is None


def test_record_and_totals(app, owner):
    from app.signature_analytics import banner_totals, record_click, record_impression

    b = _banner(owner, track=True)
    record_impression(b.id)
    record_impression(b.id)
    record_click(b.id)
    row = db.session.query(BannerStat).filter_by(banner_id=b.id).one()
    assert row.impressions == 2 and row.clicks == 1
    assert banner_totals(owner.organization_id)[b.id] == {"impressions": 2, "clicks": 1}


# ── resolver-integratie ──────────────────────────────────────────────────────
def _sig_and_account(owner):
    db.session.add(Signature(organization_id=owner.organization_id, domain=None, name="Std",
                             body="Groeten", html="<b>{{naam}}</b>"))
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email="meia@meet-x.be", status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    return acct


def test_tracking_wraps_link_and_adds_pixel(app, owner):
    from app.signatures import resolve_rich

    acct = _sig_and_account(owner)
    _banner(owner, track=True, link="https://acme.be/promo")
    html = resolve_rich(owner, account=acct).html
    assert "/r/b/" in html          # click-redirect
    assert "/t/b/" in html          # weergave-pixel
    assert "display:none" in html   # pixel verborgen


def test_no_tracking_when_off(app, owner):
    from app.signatures import resolve_rich

    acct = _sig_and_account(owner)
    _banner(owner, track=False, link="https://acme.be/promo")
    html = resolve_rich(owner, account=acct).html
    assert "/r/b/" not in html and "/t/b/" not in html
    assert "https://acme.be/promo" in html  # gewone link


# ── publieke endpoints ───────────────────────────────────────────────────────
def test_pixel_endpoint_counts_and_returns_gif(app, client, owner):
    from app.signature_analytics import banner_token

    b = _banner(owner, track=True)
    r = client.get(f"/t/b/{banner_token(b.id)}")
    assert r.status_code == 200 and r.mimetype == "image/gif"
    assert db.session.query(BannerStat).filter_by(banner_id=b.id).one().impressions == 1


def test_redirect_endpoint_counts_and_redirects(app, client, owner):
    from app.signature_analytics import banner_token

    b = _banner(owner, track=True)
    target = "https://acme.be/promo"
    r = client.get(f"/r/b/{banner_token(b.id)}?u={urllib.parse.quote(target)}")
    assert r.status_code == 302 and r.headers["Location"] == target
    assert db.session.query(BannerStat).filter_by(banner_id=b.id).one().clicks == 1


def test_redirect_ignores_the_url_parameter(app, client, owner):
    """The target always comes from the banner itself — a forged ?u= is never followed,
    so the public click endpoint is not an open redirect."""
    from app.signature_analytics import banner_token

    b = _banner(owner, track=True)
    for forged in ("https://evil.example/phish", "javascript:alert(1)"):
        r = client.get(f"/r/b/{banner_token(b.id)}?u={urllib.parse.quote(forged)}")
        assert r.status_code == 302 and r.headers["Location"] == "https://acme.be/promo"


def test_redirect_with_invalid_token_goes_home(app, client):
    r = client.get("/r/b/onzin?u=" + urllib.parse.quote("https://evil.example/"))
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_redirect_for_banner_without_http_link_goes_home(app, client, owner):
    from app.signature_analytics import banner_token

    b = _banner(owner, track=True, link="javascript:alert(1)")
    r = client.get(f"/r/b/{banner_token(b.id)}")
    assert r.status_code == 302 and "javascript:" not in r.headers["Location"]
    assert db.session.query(BannerStat).filter_by(banner_id=b.id).count() == 0  # niet geteld


def test_pixel_bad_token_still_gif(app, client):
    r = client.get("/t/b/onzin")
    assert r.status_code == 200 and r.mimetype == "image/gif"

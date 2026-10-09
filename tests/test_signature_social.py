"""Social-block: e-mailveilige profiel-links uit de brand kit via {{social}}.

Bewust tekst-links (geen SVG/gehoste iconen) → betrouwbaar in élke mailclient. Alleen
geldige http(s)-netwerken verschijnen; niets ingevuld → lege string (placeholder verdwijnt).
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import BrandKit, MailAccount
from app.signatures import render_placeholders, social_html


@pytest.fixture()
def owner(app):
    user = create_company("Soc BV", "Soo Soc", "soo@soc-a.be", "geheim123")
    user.email_verified = user.onboarded = True
    if user.style_profile:
        user.style_profile.signature = None
    db.session.commit()
    return user


def _kit(owner, domain=None, socials=None, color="#112233"):
    k = BrandKit(organization_id=owner.organization_id, domain=domain, name=None,
                 logo_url=None, accent_color=color, socials=socials)
    db.session.add(k)
    db.session.commit()
    return k


def _account(owner, email="soo@soc-a.be"):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected", mailbox_type="personal")
    db.session.add(acct)
    db.session.commit()
    return acct


# ── social_html ──────────────────────────────────────────────────────────────
def test_social_html_renders_only_filled_networks(app):
    kit = BrandKit(accent_color="#6C4CE6",
                   socials={"linkedin": "https://linkedin.com/company/soc", "x": "", "facebook": "not-a-url"})
    html = social_html(kit)
    assert "LinkedIn" in html and "linkedin.com/company/soc" in html
    assert "Facebook" not in html   # ongeldige URL geweerd
    assert "#6C4CE6" in html        # accentkleur toegepast


def test_social_html_empty_when_none(app):
    assert social_html(BrandKit(socials=None)) == ""
    assert social_html(BrandKit(socials={})) == ""
    assert social_html(None) == ""


def test_social_html_escapes_url(app):
    kit = BrandKit(socials={"linkedin": 'https://x/"><script>'})
    html = social_html(kit)
    assert "<script>" not in html and "&quot;" in html


# ── placeholder-integratie ───────────────────────────────────────────────────
def test_placeholder_social_is_raw_html_in_html_mode(app, owner):
    _kit(owner, domain=None, socials={"linkedin": "https://linkedin.com/in/soo"})
    acct = _account(owner)
    out = render_placeholders('<div data-ph="social">{{social}}</div>', owner, acct, html=True)
    assert '<a href="https://linkedin.com/in/soo"' in out   # niet ge-escaped


def test_placeholder_social_empty_strips_element(app, owner):
    _kit(owner, domain=None, socials=None)
    acct = _account(owner)
    out = render_placeholders('<div data-ph="social">{{social}}</div>', owner, acct, html=True)
    assert "data-ph" not in out and out.strip() == ""


def test_placeholder_social_text_mode_strips_tags(app, owner):
    _kit(owner, domain=None, socials={"linkedin": "https://linkedin.com/in/soo"})
    acct = _account(owner)
    out = render_placeholders("Volg ons: {{social}}", owner, acct, html=False)
    assert "<a" not in out and "LinkedIn" in out


def test_domain_kit_socials_win(app, owner):
    _kit(owner, domain=None, socials={"linkedin": "https://linkedin.com/default"})
    _kit(owner, domain="soc-a.be", socials={"linkedin": "https://linkedin.com/merk-a"})
    acct = _account(owner, email="soo@soc-a.be")
    out = render_placeholders("{{social}}", owner, acct, html=True)
    assert "linkedin.com/merk-a" in out

"""Banner-targeting: doelbereik per domein / afdeling / M365-groep.

Een tenant met meerdere domeinen/afdelingen/groepen moet per doelgroep een eigen banner
kunnen tonen. active_banner_html filtert op de context van de verzender.
"""
from __future__ import annotations

import pytest

from app.auth.service import create_company
from app.extensions import db
from app.models import MailAccount, SignatureBanner
from app.signatures import active_banner_html


@pytest.fixture()
def owner(app):
    user = create_company("Scope BV", "Sam Scope", "sam@merk-a.be", "geheim123")
    user.email_verified = user.onboarded = True
    user.department = "Sales"
    db.session.commit()
    return user


def _account(owner, email="sam@merk-a.be", groups=None):
    acct = MailAccount(user_id=owner.id, organization_id=owner.organization_id,
                       provider="outlook", email=email, status="connected",
                       mailbox_type="personal", graph_group_ids=groups or [])
    db.session.add(acct)
    db.session.commit()
    return acct


def _banner(owner, name, kind="org", value=None, weight=0):
    b = SignatureBanner(organization_id=owner.organization_id, name=name,
                        image_url=f"https://x/{name}.png", enabled=True,
                        scope_kind=kind, scope_value=value, weight=weight)
    db.session.add(b)
    db.session.commit()
    return b


def _active_name(owner, acct):
    res = active_banner_html(owner.organization, user=owner, account=acct)
    if not res:
        return None
    import re
    m = re.search(r"https://x/([^.]+)\.png", res[0])
    return m.group(1) if m else None


def test_org_banner_always_matches(app, owner):
    acct = _account(owner)
    _banner(owner, "algemeen", kind="org")
    assert _active_name(owner, acct) == "algemeen"


def test_domain_scope(app, owner):
    acct = _account(owner, email="sam@merk-a.be")
    _banner(owner, "merk-a", kind="domain", value="merk-a.be", weight=5)
    _banner(owner, "merk-b", kind="domain", value="merk-b.be", weight=5)
    # Verzendt vanaf merk-a.be → alleen de merk-a-banner matcht.
    assert _active_name(owner, acct) == "merk-a"


def test_department_scope(app, owner):
    acct = _account(owner)  # owner.department == "Sales"
    _banner(owner, "sales", kind="department", value="Sales", weight=5)
    _banner(owner, "hr", kind="department", value="HR", weight=5)
    assert _active_name(owner, acct) == "sales"


def test_group_scope(app, owner):
    acct = _account(owner, groups=["GID-SALES", "GID-ALL"])
    _banner(owner, "salesgroep", kind="group", value="gid-sales", weight=5)
    _banner(owner, "mktgroep", kind="group", value="gid-mkt", weight=5)
    assert _active_name(owner, acct) == "salesgroep"


def test_targeted_beats_org_by_weight(app, owner):
    acct = _account(owner)
    _banner(owner, "algemeen", kind="org", weight=1)
    _banner(owner, "sales", kind="department", value="Sales", weight=9)
    # Beide matchen; hoogste weging (de gerichte) wint.
    assert _active_name(owner, acct) == "sales"


def test_non_matching_scope_falls_back(app, owner):
    acct = _account(owner, email="sam@merk-a.be")
    _banner(owner, "algemeen", kind="org", weight=1)
    _banner(owner, "andere", kind="domain", value="ander-domein.be", weight=9)
    # De gerichte banner matcht niet (ander domein) → val terug op de org-banner.
    assert _active_name(owner, acct) == "algemeen"

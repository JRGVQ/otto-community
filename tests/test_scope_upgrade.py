"""Een nieuwe Graph-scope mag bestaande koppelingen niet stilleggen.

Een refresh-token dat is afgegeven vóór we een scope toevoegden, draagt die scope niet.
Microsoft weigert de vernieuwing dan (AADSTS65001) in plaats van hem stil weg te laten —
dus zonder opvang valt élk al gekoppeld postvak stil op het moment dat wij iets toevoegen.
"""
from __future__ import annotations

import pytest

from app.providers import outlook as O


class _MSAL:
    """Een tenant die de nieuwe scope niet kent, maar de oude wel."""

    def __init__(self, weigert: set[str]):
        self.weigert = weigert
        self.calls: list[list[str]] = []

    def acquire_token_by_refresh_token(self, _refresh, scopes):
        self.calls.append(list(scopes))
        if self.weigert & set(scopes):
            return {"error": "invalid_grant",
                    "error_description": "AADSTS65001: The user has not consented"}
        return {"access_token": "verse-token", "expires_in": 3600}


@pytest.fixture()
def account(app):
    from app.auth.service import create_company
    from app.extensions import db
    from app.models import MailAccount
    from app.security.crypto import encrypt

    user = create_company("Acme BV", "Owner", "owner@acme-scope.be", "geheim123")
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="owner@acme-scope.be", status="connected",
                       refresh_token_enc=encrypt("oud-refresh-token"))
    db.session.add(acct)
    db.session.commit()
    return acct


def _refresh(monkeypatch, account, msal):
    """msal is een optionele dependency en staat hier niet geïnstalleerd; refresh_token
    importeert hem binnenin, dus een module in sys.modules volstaat."""
    import sys
    import types

    nep = types.ModuleType("msal")
    nep.ConfidentialClientApplication = lambda *a, **k: msal
    monkeypatch.setitem(sys.modules, "msal", nep)
    monkeypatch.setattr("app.connectors.microsoft_app", lambda org: ("cid", "tid", "sec"))
    provider = O.OutlookProvider(account)
    provider.refresh_token()
    return provider


def test_the_optional_scope_is_declared_optional():
    """Zo niet, dan is de opvang hieronder dode code."""
    assert "Calendars.ReadWrite" in O.GRAPH_OPTIONAL_SCOPES
    assert "Calendars.ReadWrite" in O.GRAPH_MSAL_SCOPES


def test_a_grant_without_the_new_scope_keeps_working(app, account, monkeypatch):
    msal = _MSAL(weigert={"Calendars.ReadWrite"})
    _refresh(monkeypatch, account, msal)

    # Eerst met alles, daarna zonder de optionele scope.
    assert len(msal.calls) == 2
    assert "Calendars.ReadWrite" in msal.calls[0]
    assert "Calendars.ReadWrite" not in msal.calls[1]
    assert "Mail.ReadWrite" in msal.calls[1]      # de mailfuncties blijven overeind


def test_and_the_screen_says_what_to_do_about_it(app, account, monkeypatch):
    _refresh(monkeypatch, account, _MSAL(weigert={"Calendars.ReadWrite"}))
    assert "opnieuw" in (account.last_error or "").lower()


def test_a_full_grant_needs_only_one_call_and_shows_no_warning(app, account, monkeypatch):
    account.last_error = "iets ouds"
    msal = _MSAL(weigert=set())
    _refresh(monkeypatch, account, msal)

    assert len(msal.calls) == 1
    assert account.last_error is None


def test_a_genuinely_dead_token_still_fails(app, account, monkeypatch):
    """De opvang mag een écht verlopen koppeling niet verdoezelen."""
    from app.providers.exceptions import ProviderAuthError

    with pytest.raises(ProviderAuthError):
        _refresh(monkeypatch, account, _MSAL(weigert={"Mail.ReadWrite"}))


@pytest.mark.parametrize("result,verwacht", [
    ({"error": "invalid_grant", "error_description": "AADSTS65001: not consented"}, True),
    ({"error": "consent_required"}, True),
    ({"error": "interaction_required"}, True),
    ({"error_description": "AADSTS65001: The user or administrator has not consented"}, True),
    ({"error": "invalid_client", "error_description": "bad secret"}, False),
    ({}, False),
])
def test_only_a_consent_problem_triggers_the_fallback(result, verwacht):
    assert O._is_consent_error(result) is verwacht

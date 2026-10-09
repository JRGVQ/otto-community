"""Registering with an email tied to a soft-deleted (trashed) org gives a clear message,
instead of silently pretending a verification mail was sent — while active accounts stay
non-enumerable (still get the neutral response)."""
from __future__ import annotations

from app.extensions import db


def _register(client, email):
    return client.post(
        "/auth/register",
        data={"company": "Nieuw BV", "name": "Nova",
              "email": email, "password": "geheim123", "confirm": "geheim123"},
        follow_redirects=False,
    )


def test_register_with_trashed_org_email_shows_clear_message(client, app):
    from app.auth.service import create_company
    from app.models import User
    from app.tenancy import soft_delete_org

    owner = create_company("Beta Holding", "Bram", "bram@beta-holding.be", "geheim123")
    soft_delete_org(owner.organization)  # move to trash
    users_before = db.session.query(User).count()

    r = _register(client, "bram@beta-holding.be")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "prullenbak" in body.lower()  # the actionable message
    # No new user created for the taken address.
    assert db.session.query(User).count() == users_before


def test_register_with_active_account_email_stays_neutral(client, app):
    """An active account must NOT be revealed — neutral 'verification sent' response."""
    from app.auth.service import create_company

    create_company("Active BV", "Ana", "ana@activebv.be", "geheim123")  # not trashed

    r = _register(client, "ana@activebv.be")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "prullenbak" not in body.lower()  # no leak that the address exists

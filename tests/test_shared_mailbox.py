"""Shared (team) mailboxes: provider routing, connect flow, members-only visibility, notes."""
import urllib.error

from app.extensions import db
from app.models import MailAccount, MailboxMember, Message, User
from app.providers.outlook import GRAPH_SCOPES, OutlookProvider
from app.security.crypto import decrypt, encrypt


# ── helpers ──────────────────────────────────────────────────────────────────
def _personal(user, email=None) -> MailAccount:
    acct = MailAccount(
        user_id=user.id, organization_id=user.organization_id, provider="outlook",
        email=email or user.email, status="connected", mailbox_type="personal",
        access_token_enc=encrypt("AT"), refresh_token_enc=encrypt("RT"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _shared(user, personal, address) -> MailAccount:
    acct = MailAccount(
        user_id=user.id, organization_id=user.organization_id, provider="outlook",
        email=address, status="connected", mailbox_type="shared", target_address=address,
        delegated_via_account_id=personal.id,
    )
    db.session.add(acct)
    db.session.flush()
    db.session.add(MailboxMember(mail_account_id=acct.id, user_id=user.id))
    db.session.commit()
    return acct


def _user_in(org_id, email, name="Mate") -> User:
    u = User(email=email, name=name, organization_id=org_id, email_verified=True, onboarded=True)
    u.set_password("geheim123")
    db.session.add(u)
    db.session.commit()
    return u


def _login_company(client, company, email):
    from app.auth.service import create_company

    user = create_company(company, "Owner", email, "geheim123")
    user.email_verified = True
    user.onboarded = True
    db.session.commit()
    client.post("/auth/login", data={"email": email, "password": "geheim123"})
    return user


# ── provider routing / token indirection ──────────────────────────────────────
def test_scope_includes_shared():
    assert "Mail.ReadWrite.Shared" in GRAPH_SCOPES


def test_root_and_auth_account_resolution(app):
    from app.auth.service import create_company

    u = create_company("Root BV", "O", "o@root.be", "geheim123")
    personal = _personal(u)
    shared = _shared(u, personal, "support@root.be")

    assert OutlookProvider(personal)._root == "/me"
    assert OutlookProvider(shared)._root == "/users/support@root.be"
    # A shared mailbox borrows the connecting member's personal grant.
    assert OutlookProvider(shared)._auth_account().id == personal.id
    assert OutlookProvider(personal)._auth_account().id == personal.id


def test_shared_refresh_writes_to_parent(app, monkeypatch):
    import sys
    import types

    from app.auth.service import create_company

    u = create_company("Ref BV", "O", "o@ref.be", "geheim123")
    personal = _personal(u)
    shared = _shared(u, personal, "support@ref.be")

    # msal is a prod-only dependency (commented out in requirements.txt; refresh_token
    # imports it lazily). Inject a fake module so the test runs in dev/CI without it —
    # mirrors the openai-module fake in tests/test_go_live_security.py.
    class FakeMsal:
        def __init__(self, *a, **k):
            pass

        def acquire_token_by_refresh_token(self, rt, scopes):
            return {"access_token": "NEW_AT", "refresh_token": "NEW_RT"}

    fake_msal = types.ModuleType("msal")
    fake_msal.ConfidentialClientApplication = FakeMsal
    monkeypatch.setitem(sys.modules, "msal", fake_msal)

    OutlookProvider(shared).refresh_token()

    db.session.refresh(personal)
    db.session.refresh(shared)
    assert decrypt(personal.access_token_enc) == "NEW_AT"   # parent token rotated
    assert decrypt(personal.refresh_token_enc) == "NEW_RT"
    assert shared.access_token_enc is None                  # shared row never stores a token


def test_create_draft_targets_shared_mailbox_path(app, monkeypatch):
    from app.auth.service import create_company

    u = create_company("WB BV", "O", "o@wb.be", "geheim123")
    personal = _personal(u)
    shared = _shared(u, personal, "support@wb.be")

    calls = []
    monkeypatch.setattr(OutlookProvider, "_post",
                        lambda self, path, payload: (calls.append(path), {"id": "d1", "body": {"content": "q"}})[1])
    monkeypatch.setattr(OutlookProvider, "_patch",
                        lambda self, path, payload: (calls.append(path), {})[1])
    OutlookProvider(shared).create_draft(None, "RE: x", "antwoord", in_reply_to="MSG1")

    assert any("/users/support@wb.be/messages/MSG1/createReply" in c for c in calls)
    assert not any(c.startswith("/me/") for c in calls)


def test_refresh_tokens_skips_shared_children(app, monkeypatch):
    from app.auth.service import create_company
    from app.tasks.schedule import refresh_tokens

    u = create_company("Beat BV", "O", "o@beat.be", "geheim123")
    personal = _personal(u)
    shared = _shared(u, personal, "support@beat.be")

    refreshed = []
    monkeypatch.setattr(OutlookProvider, "refresh_token", lambda self: refreshed.append(self.account.id))
    refresh_tokens()

    assert personal.id in refreshed       # grant holder is refreshed
    assert shared.id not in refreshed     # the borrowing shared row is skipped


# ── connect flow (verify / add / leave) ───────────────────────────────────────
def test_add_shared_creates_account_and_membership(client, app, monkeypatch):
    u = _login_company(client, "Team BV", "owner@team.be")
    _personal(u, "owner@team.be")
    monkeypatch.setattr(OutlookProvider, "_get", lambda self, path: {"id": "inbox"})

    r = client.post("/api/shared-mailboxes", json={"address": "support@team.be"})
    assert r.status_code == 200 and r.get_json()["ok"]

    acct = (db.session.query(MailAccount)
            .filter_by(mailbox_type="shared", target_address="support@team.be").first())
    assert acct is not None
    assert acct.organization_id == u.organization_id
    assert u.id in acct.member_user_ids
    assert acct.delegated_via_account_id is not None


def test_add_shared_denied_creates_nothing(client, app, monkeypatch):
    u = _login_company(client, "Deny BV", "owner@deny.be")
    _personal(u, "owner@deny.be")

    def forbidden(self, path):
        raise urllib.error.HTTPError(path, 403, "Forbidden", {}, None)

    monkeypatch.setattr(OutlookProvider, "_get", forbidden)
    r = client.post("/api/shared-mailboxes", json={"address": "secret@deny.be"})
    assert r.status_code == 403
    assert db.session.query(MailAccount).filter_by(mailbox_type="shared").count() == 0


def test_second_member_joins_without_new_sync_row(app, monkeypatch):
    from flask_login import login_user

    from app.api import shared_mailboxes as sm
    from app.auth.service import create_company

    owner = create_company("Join BV", "Owner", "owner@join.be", "geheim123")
    _personal(owner, "owner@join.be")
    mate = _user_in(owner.organization_id, "mate@join.be")
    _personal(mate, "mate@join.be")
    monkeypatch.setattr(OutlookProvider, "_get", lambda self, path: {"id": "inbox"})

    # Each member adds the same shared mailbox. Driven via request contexts so each call
    # has its own identity — multiple HTTP logins share one app-context in the fixture,
    # which makes Flask-Login cache the first user.
    for u in (owner, mate):
        with app.test_request_context("/api/shared-mailboxes", method="POST", json={"address": "support@join.be"}):
            login_user(u)
            sm.add_shared()

    rows = db.session.query(MailAccount).filter_by(mailbox_type="shared", target_address="support@join.be").all()
    assert len(rows) == 1                       # joined, not duplicated
    assert {owner.id, mate.id} <= set(rows[0].member_user_ids)


# ── members-only visibility ────────────────────────────────────────────────────
def test_shared_mail_visible_to_members_only(app):
    from app.auth.service import create_company
    from flask_login import login_user

    from app.web import context as ctx

    owner = create_company("Vis BV", "Owner", "owner@vis.be", "geheim123")
    other = _user_in(owner.organization_id, "other@vis.be")
    personal = _personal(owner, "owner@vis.be")
    shared = _shared(owner, personal, "support@vis.be")
    msg = Message(organization_id=owner.organization_id, account_id=shared.id,
                  subject="Klantvraag", sender="Klant", body="hallo")
    db.session.add(msg)
    db.session.commit()

    with app.test_request_context():
        login_user(owner)
        assert msg.id in {m.id for m in ctx._messages()}    # member sees it
    with app.test_request_context():
        login_user(other)
        assert msg.id not in {m.id for m in ctx._messages()}  # non-member does not


def test_notes_are_members_only(client, app):
    from app.auth.service import create_company

    owner = create_company("Note BV", "Owner", "owner@note.be", "geheim123")
    owner.email_verified = True
    owner.onboarded = True
    db.session.commit()
    personal = _personal(owner, "owner@note.be")
    shared = _shared(owner, personal, "support@note.be")
    msg = Message(organization_id=owner.organization_id, account_id=shared.id,
                  subject="X", sender="K", body="hi")
    db.session.add(msg)
    db.session.commit()
    mid = msg.id

    other = _user_in(owner.organization_id, "other@note.be")

    # Member posts + reads a note over HTTP (single login).
    client.post("/auth/login", data={"email": "owner@note.be", "password": "geheim123"})
    assert client.post(f"/api/messages/{mid}/notes", json={"body": "intern overleg"}).get_json()["ok"]
    assert len(client.get(f"/api/messages/{mid}/notes").get_json()["notes"]) == 1

    # The membership gate (used by every notes/draft endpoint via get_message) denies a
    # non-member and admits a member.
    from app.api._helpers import get_message
    from flask_login import login_user
    from werkzeug.exceptions import NotFound

    with app.test_request_context():
        login_user(other)
        try:
            get_message(mid)
            raise AssertionError("non-member must not reach a shared mailbox's message")
        except NotFound:
            pass
    with app.test_request_context():
        login_user(owner)
        assert get_message(mid).id == mid

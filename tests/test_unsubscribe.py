"""List-Unsubscribe parsing + the unsubscribe endpoint (with logging)."""
from __future__ import annotations

from app.extensions import db
from app.models import AuditLog, Message, UnsubscribeLog
from app.unsubscribe import is_safe_public_url, one_click_post, parse_list_unsubscribe


# ── Header parsing ────────────────────────────────────────────────────────────
def test_parse_extracts_url_and_mailto():
    info = parse_list_unsubscribe(
        "<https://news.example.com/u/abc>, <mailto:unsub@example.com?subject=stop>"
    )
    assert info.url == "https://news.example.com/u/abc"
    assert info.mailto == "mailto:unsub@example.com?subject=stop"
    assert info.available is True
    assert info.one_click is False  # no -Post header


def test_parse_one_click_requires_post_header_and_https():
    info = parse_list_unsubscribe(
        "<https://x.example.com/u>", "List-Unsubscribe=One-Click"
    )
    assert info.one_click is True

    # http (not https) never qualifies for one-click, even with the -Post header.
    http = parse_list_unsubscribe("<http://x.example.com/u>", "List-Unsubscribe=One-Click")
    assert http.one_click is False


def test_parse_empty_header():
    info = parse_list_unsubscribe(None)
    assert info.available is False
    assert info.url is None and info.mailto is None


# ── SSRF guard ────────────────────────────────────────────────────────────────
def test_safe_url_rejects_private_and_non_https():
    assert is_safe_public_url("http://example.com/u") is False       # not https
    assert is_safe_public_url("https://127.0.0.1/u") is False        # loopback
    assert is_safe_public_url("https://localhost/u") is False        # loopback name
    assert is_safe_public_url("https://10.0.0.5/u") is False         # private
    assert is_safe_public_url("ftp://example.com") is False          # wrong scheme


def test_one_click_post_refuses_unsafe_url_without_network():
    ok, detail = one_click_post("https://127.0.0.1/u")
    assert ok is False
    assert "onveilig" in detail or "onbereikbaar" in detail


def test_one_click_does_not_follow_redirects():
    """Redirects must NOT be followed — only the initial host is SSRF-validated, so a 30x
    to an internal address would otherwise bypass the guard (returning None = don't follow)."""
    from app.unsubscribe import _NoRedirect

    handler = _NoRedirect()
    assert handler.redirect_request(
        None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
    ) is None


# ── Endpoint: link (non one-click) ────────────────────────────────────────────
def _demo_msg_with_unsub(app):
    """The seeded newsletter carries a demo unsubscribe URL."""
    return db.session.query(Message).filter_by(sender="Smashing Magazine").first()


def test_unsubscribe_link_opens_url_and_logs(auth_client, app):
    msg = _demo_msg_with_unsub(app)
    assert msg and msg.has_unsubscribe and not msg.unsubscribe_oneclick

    r = auth_client.post(f"/api/messages/{msg.id}/unsubscribe")
    data = r.get_json()
    assert data["ok"] is True
    assert data["open_url"] == msg.unsubscribe_url

    log = db.session.query(UnsubscribeLog).filter_by(message_id=msg.id).first()
    assert log and log.method == "link" and log.status == "opened"
    assert db.session.get(Message, msg.id).unsubscribed_at is not None
    # Also recorded in the org audit trail.
    assert db.session.query(AuditLog).filter(AuditLog.action.like("%uitschrijf%")).count() >= 1


# ── Endpoint: one-click (server-side POST, mocked) ────────────────────────────
def test_unsubscribe_one_click_executes_server_side(auth_client, app, demo_user, monkeypatch):
    msg = Message(
        organization_id=demo_user.organization_id, user_id=demo_user.id,
        sender="Promo NV", sender_email="news@promo.example",
        subject="Deals", snippet="…", body="…",
        unsubscribe_url="https://promo.example/u/xyz", unsubscribe_oneclick=True,
    )
    db.session.add(msg)
    db.session.commit()

    monkeypatch.setattr("app.unsubscribe.one_click_post", lambda url, timeout=6.0: (True, "HTTP 200"))
    r = auth_client.post(f"/api/messages/{msg.id}/unsubscribe")
    data = r.get_json()
    assert data["ok"] is True and "open_url" not in data
    assert data.get("reload") is True

    log = db.session.query(UnsubscribeLog).filter_by(message_id=msg.id).first()
    assert log.method == "one_click" and log.status == "done"
    assert db.session.get(Message, msg.id).unsubscribed_at is not None


def _mk(demo_user, **kw):
    base = dict(
        organization_id=demo_user.organization_id, user_id=demo_user.id,
        sender=kw.pop("sender", "Promo NV"), subject="Deals", snippet="", body="",
    )
    return Message(**{**base, **kw})


# ── Bulk endpoint ─────────────────────────────────────────────────────────────
def test_bulk_unsubscribe_one_click_and_leaves_manual(auth_client, app, demo_user, monkeypatch):
    oc = _mk(demo_user, sender="Promo NV", sender_email="news@promo.example",
             unsubscribe_url="https://promo.example/u", unsubscribe_oneclick=True)
    db.session.add(oc)
    db.session.commit()

    monkeypatch.setattr("app.unsubscribe.one_click_post", lambda url, timeout=6.0: (True, "HTTP 200"))
    r = auth_client.post("/api/messages/unsubscribe-all", json={"cat": "alle"})
    data = r.get_json()
    assert data["ok"] is True and data.get("reload") is True

    # The one-click sender was unsubscribed server-side and logged.
    assert db.session.get(Message, oc.id).unsubscribed_at is not None
    log = db.session.query(UnsubscribeLog).filter_by(message_id=oc.id, method="one_click").first()
    assert log and log.status == "done"

    # The seeded link-only newsletter can't be bulk-handled → left for the per-mail button.
    sm = db.session.query(Message).filter_by(sender="Smashing Magazine").first()
    assert sm.unsubscribed_at is None


def test_bulk_unsubscribe_respects_category_scope(auth_client, app, demo_user, monkeypatch):
    from app.models import Category

    cat = (db.session.query(Category)
           .filter_by(organization_id=demo_user.organization_id, key="nieuwsbrief").first())
    assert cat, "seed should provide the Nieuwsbrief/Marketing category"

    inside = _mk(demo_user, sender="In Cat", sender_email="a@promo.example",
                 unsubscribe_url="https://a.example/u", unsubscribe_oneclick=True, category_id=cat.id)
    outside = _mk(demo_user, sender="No Cat", sender_email="b@promo.example",
                  unsubscribe_url="https://b.example/u", unsubscribe_oneclick=True)
    db.session.add_all([inside, outside])
    db.session.commit()

    monkeypatch.setattr("app.unsubscribe.one_click_post", lambda url, timeout=6.0: (True, "HTTP 200"))
    r = auth_client.post("/api/messages/unsubscribe-all", json={"cat": "nieuwsbrief"})
    assert r.get_json()["ok"] is True

    assert db.session.get(Message, inside.id).unsubscribed_at is not None   # in scope
    assert db.session.get(Message, outside.id).unsubscribed_at is None      # different category


def test_unsubscribe_no_data_is_logged_and_400(auth_client, app, demo_user):
    msg = Message(
        organization_id=demo_user.organization_id, user_id=demo_user.id,
        sender="Plain", sender_email="a@b.example", subject="hi", snippet="", body="",
    )
    db.session.add(msg)
    db.session.commit()

    r = auth_client.post(f"/api/messages/{msg.id}/unsubscribe")
    assert r.status_code == 400
    log = db.session.query(UnsubscribeLog).filter_by(message_id=msg.id).first()
    assert log and log.status == "no_data"

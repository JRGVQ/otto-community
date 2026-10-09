"""Comprehensive request/error logging + owner Logboek + configurable retention."""
from __future__ import annotations

from datetime import timedelta

from app.extensions import db
from app.models import ErrorLog, RequestLog
from app.models.base import utcnow
from app.seed.demo_data import DEMO_PASSWORD


def _login_owner(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def test_requests_are_logged_when_enabled(client, app):
    app.config["REQUEST_LOG_ENABLED"] = True
    before = db.session.query(RequestLog).count()
    assert client.get("/healthz").status_code == 200  # skipped endpoint → not logged
    client.get("/auth/login")                          # a real page → logged
    rows = db.session.query(RequestLog).count()
    assert rows > before
    latest = db.session.query(RequestLog).order_by(RequestLog.id.desc()).first()
    assert latest.path == "/auth/login"
    assert latest.status_code == 200
    assert latest.duration_ms is not None


def test_healthz_and_static_are_not_logged(client, app):
    app.config["REQUEST_LOG_ENABLED"] = True
    db.session.query(RequestLog).delete()
    db.session.commit()
    client.get("/healthz")
    assert db.session.query(RequestLog).filter_by(path="/healthz").count() == 0


def test_error_is_captured_on_independent_session(auth_client, app):
    """A 500 lands in the ErrorLog even though it's written on a separate session — and a
    matching access-log row records the failing request too."""
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["REQUEST_LOG_ENABLED"] = True
    err_before = db.session.query(ErrorLog).count()

    r = auth_client.get("/owner/debug/test-error")
    assert r.status_code == 500
    assert db.session.query(ErrorLog).count() == err_before + 1
    # The access log also recorded the 5xx request (so even traceback-less timeouts leave a trail).
    hit = (db.session.query(RequestLog)
           .filter_by(path="/owner/debug/test-error", status_code=500).count())
    assert hit >= 1


def test_retention_prune_removes_old_rows(app):
    with app.app_context():
        from app.platform_settings import set_log_retention_days
        from app.tasks.schedule import purge_old_logs

        set_log_retention_days(30)
        old = utcnow() - timedelta(days=40)
        recent = utcnow() - timedelta(days=5)
        db.session.add(RequestLog(path="/old", status_code=200, occurred_at=old))
        db.session.add(RequestLog(path="/recent", status_code=200, occurred_at=recent))
        db.session.add(ErrorLog(path="/old-err", status_code=500, occurred_at=old))
        db.session.commit()

        removed = purge_old_logs()
        assert removed >= 2
        assert db.session.query(RequestLog).filter_by(path="/old").count() == 0
        assert db.session.query(RequestLog).filter_by(path="/recent").count() == 1
        assert db.session.query(ErrorLog).filter_by(path="/old-err").count() == 0


def test_owner_logboek_renders_and_retention_is_configurable(client, app):
    _login_owner(client)
    r = client.get("/owner/logs")
    assert r.status_code == 200
    assert "Logboek" in r.get_data(as_text=True)

    # Change the retention window via the backoffice form.
    client.post("/owner/logs/retention", data={"days": "14"})
    with app.app_context():
        from app.platform_settings import log_retention_days
        assert log_retention_days() == 14

    # Out-of-range is clamped, never 500.
    client.post("/owner/logs/retention", data={"days": "9999"})
    with app.app_context():
        from app.platform_settings import log_retention_days
        assert log_retention_days() == 365


# ── Geen klantmail in het foutenlog (regressie) ──────────────────────────────
def test_sql_payloads_are_redacted_from_errors():
    """SQLAlchemy hangt bij een DBAPI-fout de query én haar parameters aan de melding.

    Bij Otto zitten daar onderwerp, afzender en de volledige concepttekst in. Die tekst
    werd integraal opgeslagen (leesbaar voor de platform-eigenaar, cross-tenant) én bij een
    foutenpiek in een GitHub-issue geplakt — `scrub()` maskeert alleen e-mailadressen.
    """
    from app.request_logging import redact_payloads

    raw = (
        "(psycopg2.errors.StringDataRightTruncation) value too long\n"
        "[SQL: INSERT INTO drafts (current_text) VALUES (%(t)s)]\n"
        "[parameters: {'t': 'Beste Jan, hierbij de offerte voor het dossier Peeters...'}]"
    )
    out = redact_payloads(raw)
    assert "Peeters" not in out and "offerte" not in out   # geen mailinhoud meer
    assert "parameters" not in out and "INSERT INTO" not in out
    assert "StringDataRightTruncation" in out              # de oorzaak blijft leesbaar
    assert "weggelaten" in out


def test_redaction_leaves_ordinary_errors_intact():
    from app.request_logging import redact_payloads

    assert redact_payloads("ZeroDivisionError: division by zero") == "ZeroDivisionError: division by zero"
    assert redact_payloads(None) == ""

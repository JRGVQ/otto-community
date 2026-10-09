"""Regressions for the outage post-mortem plus the AI model picker.

The outage that motivated these: a database hiccup made /healthz answer 503, the
container was marked unhealthy, the reverse proxy dropped its only upstream, and every
visitor got the proxy's bare "no available server" screen — an app-wide outage caused by
a fault the app itself had survived.
"""
from __future__ import annotations

import urllib.error

import pytest

from app.extensions import db
from app.models import ErrorLog
from app.models.base import utcnow
from app.seed.demo_data import DEMO_PASSWORD


# ── Liveness vs readiness ────────────────────────────────────────────────────
def test_healthz_stays_200_when_the_database_is_down(client, monkeypatch):
    """Liveness must not depend on the database — that dependency is what turned a
    transient DB fault into a total outage at the proxy."""
    monkeypatch.setattr(db.session, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.get_json()["db"] == "error"


def test_readyz_reports_503_when_the_database_is_down(client, monkeypatch):
    """Readiness is where a dependency failure belongs — for monitoring, not for the
    container healthcheck."""
    monkeypatch.setattr(db.session, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    resp = client.get("/readyz")

    assert resp.status_code == 503
    assert resp.get_json()["status"] == "degraded"


def test_readyz_is_ok_normally(client):
    assert client.get("/readyz").status_code == 200


# ── Error pages that cannot themselves fail ──────────────────────────────────
def test_error_page_falls_back_when_the_template_cannot_render(app):
    """When the DB is the thing that broke, the styled page (which loads the current
    user through base.html) raises too. The fallback must still produce a real page."""
    from app import errors

    with app.test_request_context("/boom"):
        html, code = errors.render_error(500, template="errors/does-not-exist.html")

    assert code == 500
    assert "<!doctype html>" in html.lower()
    assert "Er ging iets mis" in html


def test_fallback_page_carries_the_request_id(app):
    from app import errors

    with app.test_request_context("/boom"):
        from flask import g

        g.request_id = "abc123"
        html = errors.fallback_page(500)

    assert "abc123" in html


# ── AI model picker ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("model", "custom", "expected"),
    [
        ("claude-opus-5", "", "claude-opus-5"),
        ("__custom__", "my-azure-deployment", "my-azure-deployment"),
        ("__custom__", "  ", None),          # "Anders…" with nothing typed clears it
        ("", "", None),
    ],
)
def test_resolve_submitted(model, custom, expected):
    from app.ai.models import resolve_submitted

    assert resolve_submitted(model, custom) == expected


def test_choices_always_offer_a_custom_escape_hatch():
    from app.ai.models import CUSTOM, choices_for

    for provider in ("anthropic", "openai", "azure", "compatible", "onzin"):
        assert choices_for(provider)[-1][0] == CUSTOM


def test_azure_has_no_preset_models_because_deployments_are_customer_named():
    from app.ai.models import CUSTOM, choices_for

    assert [value for value, _ in choices_for("azure")] == [CUSTOM]


def test_saved_model_outside_the_catalogue_opens_on_custom():
    from app.ai.models import template_context

    class _Ai:
        byo_provider = "anthropic"
        byo_model = "some-model-we-do-not-list"

    ctx = template_context(_Ai())

    assert ctx["byo_model_is_custom"] is True


def test_known_model_does_not_open_on_custom():
    from app.ai.models import template_context

    class _Ai:
        byo_provider = "anthropic"
        byo_model = "claude-opus-5"

    assert template_context(_Ai())["byo_model_is_custom"] is False


def test_model_dropdown_round_trips_through_the_form(client, app):
    """Picking a model in the dropdown stores that id; picking 'Anders…' stores the
    typed value instead."""
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})

    client.post("/admin/ai/byo", data={
        "provider": "anthropic", "model": "claude-sonnet-5",
        "api_key": "sk-test", "model_custom": "", "base_url": "",
    }, follow_redirects=True)
    from app.models import User

    org = db.session.query(User).filter_by(email="demo@example.com").first().organization
    assert org.ai.byo_model == "claude-sonnet-5"

    client.post("/admin/ai/byo", data={
        "provider": "azure", "model": "__custom__",
        "model_custom": "otto-gpt4o-eu", "api_key": "", "base_url": "https://x.openai.azure.com",
    }, follow_redirects=True)
    db.session.refresh(org.ai)
    assert org.ai.byo_model == "otto-gpt4o-eu"


def test_ai_settings_screen_renders_the_dropdown(client, app):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})

    html = client.get("/admin/ai").get_data(as_text=True)

    assert 'id="byo-model"' in html
    assert "claude-opus-5" in html
    assert 'name="model_custom"' in html


# ── GitHub error alerting ────────────────────────────────────────────────────
def _make_errors(n: int, endpoint: str = "web.inbox", exc_type: str = "OperationalError") -> None:
    for _i in range(n):
        db.session.add(ErrorLog(
            status_code=500, method="GET", path="/app/inbox", endpoint=endpoint,
            exception_type=exc_type, message="server closed the connection unexpectedly",
            traceback="Traceback…", occurred_at=utcnow(), user_email="klant@example.com",
        ))
    db.session.commit()


def test_alerting_is_off_without_configuration(app):
    from app.alerting import check_error_spike, is_enabled

    _make_errors(50)
    assert is_enabled() is False
    assert check_error_spike() == 0


def test_spike_opens_one_issue_for_the_whole_group(app, monkeypatch):
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x",
                      ERROR_ALERT_THRESHOLD=10, ERROR_ALERT_WINDOW_MINUTES=15)
    _make_errors(12)
    created = []
    monkeypatch.setattr(alerting, "create_issue",
                        lambda title, body: created.append((title, body)) or 4242)

    assert alerting.check_error_spike() == 1
    assert len(created) == 1, "a storm of one bug must produce ONE issue, not one per error"
    title, body = created[0]
    assert "12×" in title
    assert "OperationalError" in title


def test_below_threshold_stays_quiet(app, monkeypatch):
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x",
                      ERROR_ALERT_THRESHOLD=10, ERROR_ALERT_WINDOW_MINUTES=15)
    _make_errors(9)
    monkeypatch.setattr(alerting, "create_issue", lambda *a: pytest.fail("should not report"))

    assert alerting.check_error_spike() == 0


def test_cooldown_prevents_a_second_issue_for_the_same_bug(app, monkeypatch):
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x",
                      ERROR_ALERT_THRESHOLD=10, ERROR_ALERT_WINDOW_MINUTES=15,
                      ERROR_ALERT_COOLDOWN_HOURS=24)
    _make_errors(12)
    monkeypatch.setattr(alerting, "create_issue", lambda *a: 1)
    assert alerting.check_error_spike() == 1

    monkeypatch.setattr(alerting, "create_issue", lambda *a: pytest.fail("re-reported inside cooldown"))
    assert alerting.check_error_spike() == 0


def test_customer_email_addresses_are_scrubbed_before_leaving(app, monkeypatch):
    """A GitHub issue is a far less private place than the database."""
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x",
                      ERROR_ALERT_THRESHOLD=1, ERROR_ALERT_WINDOW_MINUTES=15)
    db.session.add(ErrorLog(
        status_code=500, endpoint="web.inbox", exception_type="ValueError",
        message="kon mail van jan.peeters@klantbedrijf.be niet verwerken",
        traceback="File x, klant@example.com", occurred_at=utcnow(),
    ))
    db.session.commit()
    sent = {}
    monkeypatch.setattr(alerting, "create_issue", lambda title, body: sent.update(body=body) or 7)

    alerting.check_error_spike()

    assert "jan.peeters@klantbedrijf.be" not in sent["body"]
    assert "klant@example.com" not in sent["body"]
    assert "[e-mail]" in sent["body"]


def test_a_failed_github_call_does_not_retry_every_tick(app, monkeypatch):
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x",
                      ERROR_ALERT_THRESHOLD=10, ERROR_ALERT_WINDOW_MINUTES=15)
    _make_errors(12)
    calls = []
    monkeypatch.setattr(alerting, "create_issue", lambda *a: calls.append(1) or None)

    alerting.check_error_spike()
    alerting.check_error_spike()

    assert len(calls) == 1


def test_create_issue_survives_a_github_outage(app, monkeypatch):
    from app import alerting

    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x")

    def boom(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", boom)

    assert alerting.create_issue("t", "b") is None  # logged, never raised


def test_debug_console_shows_the_alerting_card_when_enabled(client, app):
    """The 'Nu controleren' branch only renders once alerting is configured, so it needs
    its own coverage — a broken route there would 500 the page the moment the owner
    switches alerting on."""
    from app.models import User

    user = db.session.query(User).filter_by(email="demo@example.com").first()
    user.is_platform_owner = True
    db.session.commit()
    app.config.update(GITHUB_ISSUE_REPO="your-org/otto", GITHUB_TOKEN="ghp_x")
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})

    resp = client.get("/owner/debug")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/owner/alerts/check" in html
    assert "your-org/otto" in html

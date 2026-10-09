"""Editable email templates + render, send-log + resend, owner console, favicon."""
from app.email import email_templates, render_email, send_email
from app.extensions import db
from app.models import EmailLog, EmailTemplate


def test_render_default_then_override(app):
    subject, html, _ = render_email("verify", name="Sam", link="https://x/y")
    assert subject == email_templates()["verify"]["subject"]
    assert "Sam" in html and "https://x/y" in html

    db.session.add(EmailTemplate(key="verify", subject="Hoi {{name}}", html="<p>{{name}}</p>"))
    db.session.commit()
    subject2, html2, _ = render_email("verify", name="Sam", link="x")
    # The override's HTML is wrapped in the branded shell (Otto header/footer).
    assert subject2 == "Hoi Sam"
    assert "<p>Sam</p>" in html2 and "Otto" in html2


def test_render_escapes_body_values(app):
    _, html, _ = render_email("verify", name="<script>x</script>", link="x")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_send_email_logs_every_attempt(app):
    ok = send_email("a@b.be", "Hi", "<p>h</p>", "h", template_key="verify")
    assert ok is False  # no mailer configured in tests
    log = db.session.query(EmailLog).filter_by(to_addr="a@b.be").first()
    assert log and log.status == "no_mailer" and log.template_key == "verify" and log.html == "<p>h</p>"


def test_verification_send_is_logged(app):
    from app.auth.service import create_company
    from app.email import send_verification

    user = create_company("Log BV", "Log Owner", "logowner@logbv.be", "geheim123")
    with app.test_request_context():  # send_verification builds a URL via url_for
        send_verification(user)
    log = db.session.query(EmailLog).filter_by(to_addr="logowner@logbv.be").first()
    assert log is not None and log.template_key == "verify"


def test_failed_send_records_reason(app, monkeypatch):
    import app.email as email_mod

    app.config.update(MAIL_PROVIDER="graph", GRAPH_MAIL_CLIENT_ID="id", GRAPH_MAIL_CLIENT_SECRET="s",
                      GRAPH_MAIL_TENANT_ID="t", GRAPH_MAIL_SENDER="otto@example.com")

    def boom(*a, **k):
        raise RuntimeError("Graph sendMail HTTP 403: ErrorAccessDenied")

    monkeypatch.setattr(email_mod, "_send_via_graph", boom)
    assert email_mod.send_email("x@y.be", "S", "<p>h</p>", "h", template_key="verify") is False
    log = db.session.query(EmailLog).filter_by(to_addr="x@y.be").order_by(EmailLog.id.desc()).first()
    assert log.status == "failed" and "403" in (log.error or "")


def test_owner_console_renders_with_mailer_warning(auth_client):
    body = auth_client.get("/owner/email").get_data(as_text=True)
    assert "Verzendlog" in body
    assert "geen mailer geconfigureerd" in body.lower()


def test_console_flags_missing_graph_sender(auth_client, app):
    app.config.update(MAIL_PROVIDER="graph", GRAPH_MAIL_CLIENT_ID="id", GRAPH_MAIL_CLIENT_SECRET="s",
                      GRAPH_MAIL_TENANT_ID="t", GRAPH_MAIL_SENDER=None)
    body = auth_client.get("/owner/email").get_data(as_text=True)
    assert "ontbreken" in body and "GRAPH_MAIL_SENDER" in body


def test_owner_edits_template(auth_client):
    auth_client.post("/owner/email/verify", data={"subject": "Nieuw onderwerp", "html": "<p>{{name}}</p>"})
    db.session.expire_all()
    ov = db.session.query(EmailTemplate).filter_by(key="verify").first()
    assert ov and ov.subject == "Nieuw onderwerp"


def test_owner_test_send_then_resend(auth_client):
    auth_client.post("/owner/email/verify/test", data={"to": "tester@x.be"})
    log = (db.session.query(EmailLog).filter_by(to_addr="tester@x.be")
           .order_by(EmailLog.id.desc()).first())
    assert log is not None
    auth_client.post(f"/owner/email/log/{log.id}/resend")
    assert db.session.query(EmailLog).filter_by(to_addr="tester@x.be").count() >= 2


def test_favicon_served_and_linked(client):
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200 and b"<svg" in r.data
    assert "favicon.svg" in client.get("/auth/login").get_data(as_text=True)

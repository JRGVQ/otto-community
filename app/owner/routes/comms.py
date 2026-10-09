"""Communicatie: gebeurtenissen, e-mailsjablonen, verzendlog en systeemstatus."""
from __future__ import annotations

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.extensions import db
from app.models import ErrorLog, Organization, User
from app.models.base import utcnow
from app.owner import owner_bp
from app.owner.routes._shared import _ctx


# ── E-mail: templates + send log ──────────────────────────────────────────────
@owner_bp.route("/events")
def events():
    """Cross-tenant business timeline: what happened to accounts, never what's in them.

    Deliberately built on OrgEvent rather than AuditLog: the audit trail quotes tenant
    content (senders, colleagues, knowledge sources), so it stays inside the tenant.
    OrgEvent uses a fixed catalogue of account facts, which is what the backoffice needs
    to follow the health of a customer without a cross-tenant data console.
    """
    from app.events import EVENT_GROUPS, GROUP_LABELS, label_for, recent_events
    from app.tenancy import active_orgs

    group = request.args.get("group") or None
    org_id = request.args.get("org", type=int)
    rows = recent_events(group=group, org_id=org_id)
    orgs = active_orgs().order_by(Organization.name).all()
    return render_template(
        "owner/events.html", rows=rows, group=group, org_id=org_id, orgs=orgs,
        groups=[(k, GROUP_LABELS[k]) for k in EVENT_GROUPS], label_for=label_for,
        **_ctx("events"),
    )


@owner_bp.route("/email")
def email_console():
    from app.email import email_templates
    from app.models import EmailLog, EmailTemplate

    overrides = {t.key: t for t in db.session.query(EmailTemplate).all()}
    templates = []
    for key, tpl in email_templates().items():
        ov = overrides.get(key)
        templates.append({
            "key": key, "name": tpl["name"], "desc": tpl["desc"], "vars": tpl["vars"],
            "subject": (ov.subject if ov and ov.subject else tpl["subject"]),
            "html": (ov.html if ov and ov.html else tpl["html"]),
            "customized": bool(ov and (ov.subject or ov.html)),
        })
    logs = db.session.query(EmailLog).order_by(EmailLog.id.desc()).limit(100).all()

    # Mailer readiness — Graph needs ALL four vars (a missing GRAPH_MAIL_SENDER still
    # "looks" configured but fails with ErrorInvalidUser 'None').
    cfg = current_app.config
    graph = cfg.get("MAIL_PROVIDER") == "graph"
    graph_vars = ("GRAPH_MAIL_CLIENT_ID", "GRAPH_MAIL_CLIENT_SECRET", "GRAPH_MAIL_TENANT_ID", "GRAPH_MAIL_SENDER")
    missing = [v for v in graph_vars if graph and not cfg.get(v)]
    mailer_on = (graph and not missing) or (bool(cfg.get("SMTP_HOST")) and not graph)
    return render_template("owner/email.html", templates=templates, logs=logs,
                           mailer_on=mailer_on, mailer_missing=missing, **_ctx("email"))


@owner_bp.route("/email/<key>", methods=["POST"])
def email_save(key: str):
    from app.email import email_templates
    from app.models import EmailTemplate

    templates = email_templates()
    if key not in templates:
        abort(404)
    tpl = db.session.query(EmailTemplate).filter_by(key=key).first()
    if tpl is None:
        tpl = EmailTemplate(key=key)
        db.session.add(tpl)
    tpl.subject = (request.form.get("subject") or "").strip() or None
    tpl.html = (request.form.get("html") or "").strip() or None
    db.session.commit()
    flash(_("Sjabloon ‘%(name)s’ opgeslagen.", name=templates[key]["name"]), "toast")
    return redirect(url_for("owner.email_console"))


@owner_bp.route("/email/<key>/reset", methods=["POST"])
def email_reset(key: str):
    """Discard the override → back to the built-in default."""
    from app.models import EmailTemplate

    ov = db.session.query(EmailTemplate).filter_by(key=key).first()
    if ov is not None:
        db.session.delete(ov)
        db.session.commit()
    flash(_("Sjabloon teruggezet op de standaard."), "toast")
    return redirect(url_for("owner.email_console"))


@owner_bp.route("/email/<key>/test", methods=["POST"])
def email_test(key: str):
    from app.email import email_templates, render_email, send_email

    templates = email_templates()
    if key not in templates:
        abort(404)
    to = (request.form.get("to") or current_user.email or "").strip()
    subject, html, text = render_email(key, **templates[key].get("sample", {}))
    ok = send_email(to, f"[TEST] {subject}", html, text, template_key=key)
    status = _("verzonden") if ok else _("niet verzonden (geen mailer/fout) — zie log")
    flash(_("Testmail naar %(to)s — %(status)s.", to=to, status=status),
          "toast" if ok else "error")
    return redirect(url_for("owner.email_console"))


@owner_bp.route("/email/log/<int:log_id>/resend", methods=["POST"])
def email_resend(log_id: int):
    from app.email import send_email
    from app.models import EmailLog

    log = db.session.get(EmailLog, log_id)
    if not log:
        abort(404)
    ok = send_email(log.to_addr, log.subject, log.html or "", log.text or "", template_key=log.template_key)
    status = _("verzonden") if ok else _("niet verzonden — zie log")
    flash(_("Opnieuw verstuurd naar %(to)s — %(status)s.", to=log.to_addr, status=status),
          "toast" if ok else "error")
    return redirect(url_for("owner.email_console"))


@owner_bp.route("/health")
def health():
    from datetime import timedelta

    from sqlalchemy import text


    cfg = current_app.config
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        db.session.execute(text("SELECT 1"))
        add("Database", True, cfg["SQLALCHEMY_DATABASE_URI"].split("://")[0])
    except Exception as e:  # noqa: BLE001
        add("Database", False, str(e)[:80])

    if cfg.get("REDIS_URL"):
        try:
            import redis

            redis.from_url(cfg["REDIS_URL"]).ping()
            add("Redis / taakwachtrij", True, "verbonden")
        except Exception as e:  # noqa: BLE001
            add("Redis / taakwachtrij", False, str(e)[:80])
    else:
        add("Taakuitvoering", True, "inline (eager) — geen Redis geconfigureerd")

    add("LLM", True, "stub (offline)" if cfg.get("LLM_IS_STUB") else cfg.get("LLM_PROVIDER"))
    # Name what is missing rather than just "demo-modus": on a red row the admin's next
    # question is always "which setting?", and the answer is a config lookup away.
    if not cfg.get("GMAIL_CONFIGURED"):
        gmail_detail = "demo-modus — GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ontbreken"
    elif cfg.get("GMAIL_REALTIME"):
        gmail_detail = f"geconfigureerd · realtime push via {cfg.get('GMAIL_PUBSUB_TOPIC')}"
    else:
        gmail_detail = "geconfigureerd · polling — ontbreekt: " + ", ".join(cfg.get("GMAIL_REALTIME_MISSING") or [])
    add("Gmail API", bool(cfg.get("GMAIL_CONFIGURED")), gmail_detail)
    add("Microsoft Graph", bool(cfg.get("GRAPH_CONFIGURED")),
        "geconfigureerd" if cfg.get("GRAPH_CONFIGURED") else "demo-modus — MS_CLIENT_ID/MS_CLIENT_SECRET ontbreken")
    add("SMTP / e-mail", bool(cfg.get("SMTP_CONFIGURED")), cfg.get("SMTP_HOST") or "niet geconfigureerd (links gelogd)")

    day_ago = utcnow() - timedelta(hours=24)
    stats = {
        "orgs": db.session.query(Organization).count(),
        "users": db.session.query(User).count(),
        "errors_24h": db.session.query(ErrorLog).filter(ErrorLog.occurred_at >= day_ago).count(),
        "version": cfg.get("APP_VERSION") or "dev",
    }
    return render_template("owner/health.html", checks=checks, stats=stats, **_ctx("health"))



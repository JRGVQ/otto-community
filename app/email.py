"""E-mail sending + signed verification tokens.

Without SMTP configured (dev), mails are logged and the verification link is
returned so the route can surface it — so the flow is testable offline.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app
from flask_babel import gettext as _
from itsdangerous import URLSafeTimedSerializer
from markupsafe import escape

from app.models.base import utcnow

VERIFY_MAX_AGE = 86400  # 24h
RESET_MAX_AGE = 3600  # 1h — password-reset links are short-lived on purpose
INVITE_MAX_AGE = 172800  # 48h — invite links may straddle a weekend


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="email-verify")


def make_verify_token(email: str) -> str:
    return _serializer().dumps(email.lower().strip())


def read_verify_token(token: str, max_age: int = VERIFY_MAX_AGE) -> str:
    """Return the email for a valid token; raises SignatureExpired/BadSignature otherwise."""
    return _serializer().loads(token, max_age=max_age)


# ── Invite tokens ─────────────────────────────────────────────────────────────
# Own salt so invite links can't double as verify/reset links (and vice versa).
def _invite_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="user-invite")


def make_invite_token(email: str) -> str:
    return _invite_serializer().dumps(email.lower().strip())


def read_invite_token(token: str, max_age: int = INVITE_MAX_AGE) -> str:
    """Return the email for a valid invite token; raises SignatureExpired/BadSignature."""
    return _invite_serializer().loads(token, max_age=max_age)


# ── Password-reset tokens ─────────────────────────────────────────────────────
# A distinct salt keeps reset tokens from being interchangeable with verify ones.
def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _pw_fingerprint(user) -> str:
    """Short fingerprint of the current password hash; embedded in reset tokens so a
    link self-invalidates the moment the password changes (single-use)."""
    import hashlib

    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:12]


def make_reset_token(user) -> str:
    return _reset_serializer().dumps(f"{user.email.lower().strip()}|{_pw_fingerprint(user)}")


def read_reset_token(token: str, max_age: int = RESET_MAX_AGE) -> str:
    """Return the email for a valid, unused reset token.

    Raises SignatureExpired (older than max_age) or BadSignature (tampered, or
    already used — the embedded password fingerprint no longer matches).
    """
    from itsdangerous import BadSignature

    from app.extensions import db
    from app.models import User

    raw = _reset_serializer().loads(token, max_age=max_age)
    email, _, fingerprint = raw.partition("|")
    user = db.session.query(User).filter_by(email=email).first()
    if user is None or _pw_fingerprint(user) != fingerprint:
        raise BadSignature("reset token no longer valid")
    return email


# ── Editable templates ────────────────────────────────────────────────────────
# Every transactional mail Otto sends. The owner can override subject + HTML in the
# backoffice (EmailTemplate); these are the built-in defaults. {{var}} placeholders
# are filled at send time (HTML-escaped in the body, plain in the subject).
# The inner HTML is the message content only — render_email() wraps it in the shared
# branded shell (app/templates/email/base.html: Otto header + footer). Buttons use _BTN.
_BTN = ("display:inline-block;background:#6C4CE6;color:#fff;padding:12px 22px;"
        "border-radius:11px;text-decoration:none;font-weight:700")

def email_templates() -> dict[str, dict]:
    """The built-in transactional mail templates, localized to the current UI language.

    Built per-call (not a module constant) so the human text resolves through gettext
    for the *triggering* request's locale — verify/reset/invite/env_deleted are all sent
    from a web request, so `_()` picks up the visitor's language. `{{var}}` placeholders
    are kept literal (filled by _subst at send time); only the surrounding text is a msgid.
    An owner override (EmailTemplate in the DB) still wins over these when present.
    """
    _hd = "color:#6C4CE6;margin:0 0 12px;font-size:20px;"
    _mut = "color:#6B6B78;font-size:13px"
    return {
        "verify": {
            "name": _("E-mailverificatie"),
            "desc": _("Naar nieuwe gebruikers om hun e-mailadres te bevestigen."),
            "vars": ["name", "link"],
            "sample": {"name": "Voorbeeld", "link": "https://otto.example.com/auth/verify/voorbeeld"},
            "subject": _("Bevestig je e-mailadres voor Otto"),
            "html": (
                f"<h2 style='{_hd}'>" + _("Welkom bij Otto 👋") + "</h2>"
                "<p>" + _("Hallo {{name}},") + "</p>"
                "<p>" + _("Bevestig je e-mailadres om je Otto-omgeving te activeren.") + "</p>"
                "<p><a href='{{link}}' style='" + _BTN + "'>" + _("E-mailadres bevestigen") + "</a></p>"
                f"<p style='{_mut}'>" + _("Of plak deze link:") + " {{link}}<br>"
                + _("De link verloopt na 24 uur.") + "</p>"
            ),
        },
        "reset": {
            "name": _("Wachtwoord opnieuw instellen"),
            "desc": _("Naar gebruikers die een wachtwoord-reset aanvragen."),
            "vars": ["name", "link"],
            "sample": {"name": "Voorbeeld", "link": "https://otto.example.com/auth/reset/voorbeeld"},
            "subject": _("Stel je Otto-wachtwoord opnieuw in"),
            "html": (
                f"<h2 style='{_hd}'>" + _("Wachtwoord opnieuw instellen") + "</h2>"
                "<p>" + _("Hallo {{name}},") + "</p>"
                "<p>" + _("We ontvingen een verzoek om je Otto-wachtwoord opnieuw in te stellen. "
                         "Kies hieronder een nieuw wachtwoord.") + "</p>"
                "<p><a href='{{link}}' style='" + _BTN + "'>" + _("Nieuw wachtwoord kiezen") + "</a></p>"
                f"<p style='{_mut}'>" + _("Of plak deze link:") + " {{link}}<br>"
                + _("De link verloopt na 1 uur. Heb je dit niet aangevraagd? Negeer deze e-mail.") + "</p>"
            ),
        },
        "invite": {
            "name": _("Teamlid-uitnodiging"),
            "desc": _("Naar een collega die door een beheerder wordt uitgenodigd."),
            "vars": ["inviter", "company", "link"],
            "sample": {"inviter": "Sam", "company": "Acme NV",
                       "link": "https://otto.example.com/auth/invite/voorbeeld"},
            "subject": _("{{inviter}} nodigt je uit voor Otto bij {{company}}"),
            "html": (
                f"<h2 style='{_hd}'>" + _("Je bent uitgenodigd 🎉") + "</h2>"
                "<p>" + _("Hallo,") + "</p>"
                "<p>" + _("{{inviter}} heeft je uitgenodigd om mee te werken in de Otto-omgeving van "
                         "<strong>{{company}}</strong>. Otto sorteert je mail en zet conceptantwoorden "
                         "in jouw stijl klaar.") + "</p>"
                "<p><a href='{{link}}' style='" + _BTN + "'>" + _("Uitnodiging accepteren") + "</a></p>"
                f"<p style='{_mut}'>" + _("Of plak deze link:") + " {{link}}<br>"
                + _("De link verloopt na 48 uur. Verwacht je deze uitnodiging niet? Negeer deze e-mail.") + "</p>"
            ),
        },
        "env_deleted": {
            "name": _("Omgeving verwijderd (bevestiging)"),
            "desc": _("Naar de eigenaar wanneer die zijn eigen Otto-omgeving verwijdert."),
            "vars": ["name", "company", "restore_days"],
            "sample": {"name": "Sam", "company": "Acme NV", "restore_days": "30"},
            "subject": _("Je Otto-omgeving voor {{company}} is verwijderd"),
            "html": (
                f"<h2 style='{_hd}'>" + _("Omgeving verwijderd") + "</h2>"
                "<p>" + _("Hallo {{name}},") + "</p>"
                "<p>" + _("Je Otto-omgeving voor <strong>{{company}}</strong> is verwijderd. We bewaren je "
                         "gegevens nog <strong>{{restore_days}} dagen</strong> — daarna worden ze definitief "
                         "gewist.") + "</p>"
                "<p>" + _("Van gedacht veranderd? Vraag de beheerder van je Otto-installatie binnen die "
                         "termijn om alles terug te zetten.") + "</p>"
            ),
        },
    }


def wrap_branded(inner_html: str, *, preheader: str | None = None) -> str:
    """Wrap already-rendered inner HTML in the shared branded e-mail shell, so every
    Otto mail looks the same.

    Built as a plain string (no Jinja) so it works in a background thread with only an
    app context — render_template's context processors need a request.
    """
    from urllib.parse import urlparse

    cfg = current_app.config
    base = (cfg.get("PUBLIC_BASE_URL") or "").rstrip("/")
    host = escape(urlparse(base).netloc or base)
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>'
           if preheader else "")
    link = (f'<div style="margin-top:8px;"><a href="{escape(base)}" style="color:#6C4CE6;'
            f'text-decoration:none;">{host}</a></div>' if base else "")
    from flask_babel import get_locale

    lang = str(get_locale() or cfg["BABEL_DEFAULT_LOCALE"])
    return (
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#F6F6FA;">'
        f'{pre}'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F6F6FA;padding:24px 12px;"><tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:100%;background:#FFFFFF;border-radius:16px;overflow:hidden;'
        "box-shadow:0 12px 40px rgba(40,30,80,.07);font-family:'Segoe UI',Helvetica,Arial,sans-serif;\">"
        # Header
        '<tr><td style="padding:24px 32px 8px;">'
        '<span style="display:inline-block;width:30px;height:30px;background:#6C4CE6;border-radius:9px;'
        'text-align:center;line-height:30px;vertical-align:middle;">'
        '<span style="display:inline-block;width:11px;height:11px;background:#FFFFFF;border-radius:50%;'
        'vertical-align:middle;"></span></span>'
        '<span style="font-size:20px;font-weight:800;letter-spacing:-.02em;color:#1B1B22;'
        'vertical-align:middle;padding-left:10px;">Otto</span>'
        '</td></tr>'
        # Body
        '<tr><td style="padding:12px 32px 24px;color:#1B1B22;font-size:15px;line-height:1.6;">'
        f'{inner_html}'
        '</td></tr>'
        # Footer
        '<tr><td style="padding:20px 32px 28px;border-top:1px solid #ECECF2;color:#9A9AA6;'
        'font-size:12px;line-height:1.6;">'
        '<div style="font-weight:700;color:#6B6B78;">Otto</div>'
        f'{link}'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _subst(template_str: str, ctx: dict, *, as_html: bool) -> str:
    """Fill {{var}} placeholders. Body values are HTML-escaped; subject values are
    kept plain but stripped of newlines (no header injection)."""
    out = template_str or ""
    for key, value in ctx.items():
        val = "" if value is None else str(value)
        val = str(escape(val)) if as_html else val.replace("\n", " ").replace("\r", " ")
        out = out.replace("{{" + key + "}}", val)
    return out


def render_email(key: str, **ctx) -> tuple[str, str, str]:
    """Render (subject, html, text) for a template key, applying the owner's override
    when present, else the built-in default. Text is derived from the rendered HTML."""
    from app.extensions import db
    from app.htmltext import html_to_text
    from app.models import EmailTemplate

    tpl = email_templates().get(key, {})
    subject, html = tpl.get("subject", ""), tpl.get("html", "")
    override = db.session.query(EmailTemplate).filter_by(key=key).first()
    if override is not None:
        if override.subject:
            subject = override.subject
        if override.html:
            html = override.html
    subject = _subst(subject, ctx, as_html=False)
    html = _subst(html, ctx, as_html=True)
    html = wrap_branded(html)  # brand shell: Otto header + footer
    return subject, html, html_to_text(html)


def _record_email(to: str, subject: str, html: str, text: str, template_key: str | None,
                  status: str, error: str | None = None) -> None:
    """Persist one EmailLog row. Best-effort — never breaks the send flow."""
    try:
        from app.extensions import db
        from app.models import EmailLog

        db.session.add(EmailLog(to_addr=to, subject=subject, template_key=template_key,
                                status=status, error=error, html=html, text=text))
        db.session.commit()
    except Exception:  # noqa: BLE001
        current_app.logger.exception("[email] could not record EmailLog")
        try:
            from app.extensions import db

            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass


def send_email(to: str, subject: str, html: str, text: str, *, template_key: str | None = None) -> bool:
    """Send a mail and log the attempt. Returns True if transmitted, else False (never raises).

    Backend chosen by MAIL_PROVIDER: Microsoft 365 via Graph (app-only sendMail) or SMTP.
    Every attempt is recorded in EmailLog (sent / failed / no_mailer) for the backoffice.
    """
    cfg = current_app.config
    status, error = "no_mailer", None
    try:
        if cfg.get("MAIL_PROVIDER") == "graph" and cfg.get("GRAPH_MAIL_CLIENT_ID"):
            _send_via_graph(to, subject, html, text)
            status = "sent"
        elif cfg.get("SMTP_HOST"):
            _send_via_smtp(to, subject, html, text)
            status = "sent"
        else:
            current_app.logger.warning("[email] No mailer configured — '%s' to %s not sent.", subject, to)
    except Exception as exc:  # noqa: BLE001 — record the reason, never break the caller
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
        current_app.logger.exception("[email] send to %s failed", to)
    _record_email(to, subject, html, text, template_key, status, error)
    return status == "sent"


def _send_via_smtp(to: str, subject: str, html: str, text: str) -> None:
    """Raises on failure; send_email captures the reason into the log."""
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as s:
        if cfg.get("SMTP_USE_TLS"):
            s.starttls()
        if cfg.get("SMTP_USER"):
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        s.send_message(msg)


def _send_via_graph(to: str, subject: str, html: str, text: str) -> None:
    """Microsoft 365 via Graph app-only sendMail (Mail.Send application permission).

    M365 retired basic-auth SMTP (March 2026); this dedicated platform app's Mail.Send
    is restricted to GRAPH_MAIL_SENDER via an Application Access Policy. Raises a
    RuntimeError with the provider's reason on failure (token error or Graph HTTP body),
    so send_email records exactly why it failed.
    """
    import json
    import urllib.error
    import urllib.request

    from msal import ConfidentialClientApplication

    cfg = current_app.config
    client = ConfidentialClientApplication(
        cfg["GRAPH_MAIL_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{cfg['GRAPH_MAIL_TENANT_ID']}",
        client_credential=cfg["GRAPH_MAIL_CLIENT_SECRET"],
    )
    token = client.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in token:
        raise RuntimeError(f"Graph-token geweigerd: {token.get('error_description') or token.get('error') or token}")
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": False,
    }
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/users/{cfg['GRAPH_MAIL_SENDER']}/sendMail",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"},
    )
    try:
        # Never unbounded: sending a verification mail happens inside a web request.
        with urllib.request.urlopen(req, timeout=cfg.get("GRAPH_TIMEOUT_SECONDS") or 20) as resp:  # noqa: S310 - trusted Graph host
            if resp.status not in (200, 202):
                raise RuntimeError(f"Graph sendMail HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Graph sendMail HTTP {exc.code}: {detail}") from exc


def send_verification(user) -> str | None:
    """Send the verification mail. Returns the link when not transmitted (dev), else None."""
    from app.extensions import db
    from app.site import app_url

    link = app_url("auth.verify_email", token=make_verify_token(user.email))
    subject, html, text = render_email("verify", name=user.name, link=link)
    sent = send_email(user.email, subject, html, text, template_key="verify")
    user.verify_sent_at = utcnow()
    db.session.commit()
    return None if sent else link


def send_password_reset(user) -> str | None:
    """Send the password-reset mail. Returns the link when not transmitted (dev), else None."""
    from app.site import app_url

    link = app_url("auth.reset_password", token=make_reset_token(user))
    subject, html, text = render_email("reset", name=user.name, link=link)
    sent = send_email(user.email, subject, html, text, template_key="reset")
    return None if sent else link


def notify_platform_owner(subject: str, body_html: str) -> bool:
    """Send an internal ops notice to the instance administrator(s).

    Goes to PLATFORM_OWNER_EMAIL when set, otherwise to every account flagged as
    administrator (is_platform_owner). Best-effort: returns False (and logs) when there
    is no recipient or no mailer, so the caller's own action still completes."""
    to = current_app.config.get("PLATFORM_OWNER_EMAIL")
    recipients = [to] if to else []
    if not recipients:
        from app.extensions import db
        from app.models import User

        recipients = [u.email for u in db.session.query(User).filter(User.is_platform_owner.is_(True))
                      if u.email]
    if not recipients:
        return False
    try:
        html = wrap_branded(body_html)
        from app.htmltext import html_to_text
        text = html_to_text(html)
        return all([send_email(r, subject, html, text, template_key="platform_notice") for r in recipients])
    except Exception as exc:  # noqa: BLE001 — a notice must never break the triggering action
        current_app.logger.warning("admin notice skipped: %s", exc)
        return False


def send_invite(user, inviter) -> str | None:
    """Send the teammate-invite mail. Returns the link when not transmitted (dev), else None."""
    from app.extensions import db
    from app.site import app_url

    link = app_url("auth.accept_invite", token=make_invite_token(user.email))
    subject, html, text = render_email(
        "invite",
        inviter=(inviter.name or inviter.email),
        company=(inviter.organization.name if inviter.organization else "je team"),
        link=link,
    )
    sent = send_email(user.email, subject, html, text, template_key="invite")
    user.verify_sent_at = utcnow()  # reused as "invite last sent" for the resend cooldown
    db.session.commit()
    return None if sent else link

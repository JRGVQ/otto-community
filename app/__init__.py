"""Otto application factory."""
from __future__ import annotations

from flask import Flask

from app.config import INSTANCE_DIR, get_config
from app.extensions import babel, csrf, db, limiter, login_manager, migrate, select_locale

# Content-Security-Policy.
#
# script-src is nonce-based: every inline <script> carries the request's nonce, and no
# other inline script runs. That is what makes an injected <script> inert — an attacker
# cannot guess the nonce. Note this is not additive: once a nonce is present, browsers
# ignore 'unsafe-inline' entirely, which is why the templates carry no inline event
# handlers (onclick=…) at all; they use data-attributes wired up in static/js/behaviors.js.
#
# style-src deliberately keeps 'unsafe-inline': the templates style elements inline
# throughout, and inline CSS is a far weaker vector than inline script. Tightening it
# would mean restyling every screen for little security gain.
def _csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "form-action 'self'; "
        "img-src 'self' data: https:; "
        # The brand face (Plus Jakarta Sans) is self-hosted under /static/fonts, so no
        # third-party font host is contacted.
        "font-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self'"
    )


# Het Outlook-taakvenster (fase 6.7) draait in een iframe ván Outlook en laadt office.js van
# de Microsoft-CDN. Daarvoor een eigen, ruimere CSP: de office-host in script/connect, en
# frame-ancestors voor de Outlook-hosts in plaats van 'self'. Alleen deze routes krijgen
# hem — de rest van Otto houdt de strakke policy hierboven.
_OFFICE_HOSTS = ("https://appsforoffice.microsoft.com https://res.cdn.office.net")
_OUTLOOK_FRAME_ANCESTORS = (
    "https://outlook.office.com https://outlook.office365.com https://outlook.live.com "
    "https://*.outlook.com https://*.office.com https://*.microsoft365.com"
)


def _addin_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        f"frame-ancestors {_OUTLOOK_FRAME_ANCESTORS}; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}' {_OFFICE_HOSTS}; "
        f"connect-src 'self' {_OFFICE_HOSTS}"
    )


def create_app(config_name: str | None = None) -> Flask:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    app = Flask(
        __name__,
        instance_path=str(INSTANCE_DIR),
        instance_relative_config=True,
        static_folder="static",
        template_folder="templates",
    )
    app.config.from_object(get_config(config_name))

    # ── Logging ───────────────────────────────────────────────────────────
    # Zonder dit erft app.logger het root-niveau WARNING en verdwijnt ELKE logger.info()
    # vóór er een record bestaat — inclusief de hartslag van de scheduler en de tientallen
    # "overgeslagen"-regels van de Graph-calls. Dat is precies de blindheid waardoor de
    # mailsync eerder stil stil kon vallen: een schoon containerlog terwijl er niets liep.
    import logging as _logging

    _level = getattr(_logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), _logging.INFO)
    if not _logging.getLogger().handlers:
        _logging.basicConfig(level=_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        _logging.getLogger().setLevel(_level)
    app.logger.setLevel(_level)

    # Behind a reverse proxy (Caddy/nginx/Traefik): honor X-Forwarded-* so the real
    # client IP reaches Flask-Limiter and generated URLs use the external https
    # scheme. Only enabled in configs that set TRUST_PROXY (prod), so forwarded
    # headers can't be spoofed when running without a proxy.
    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ── Sentry (optional, production error tracking) ──────────────────────
    if app.config.get("SENTRY_DSN"):
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration

            sentry_sdk.init(
                dsn=app.config["SENTRY_DSN"],
                integrations=[FlaskIntegration()],
                traces_sample_rate=0.1,
                send_default_pii=False,
            )
        except Exception:  # noqa: BLE001 - never block boot on monitoring
            pass

    # ── Extensions ────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # batch mode = SQLite-safe ALTERs
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    babel.init_app(app, locale_selector=select_locale)
    # Gettext mode. Flask-Babel defaults to *newstyle*, where Jinja runs every `_()`
    # result through `% variables` — which raises ValueError on any literal '%' in the
    # text (e.g. "36% gebruikt"). We install *oldstyle* instead, and deliberately use
    # RAW translation lookups (not flask_babel.gettext/ngettext): those do their own
    # `% variables` with a hardcoded 'num' key, which double-formats and mismatches the
    # template's own placeholder names (KeyError on {% trans count=… %} pluralization).
    # With raw callables + oldstyle, Jinja alone does the interpolation, so both literal
    # '%' in bare `_()` and %(name)s in `{% trans %}` blocks work correctly.
    from flask_babel import get_translations

    def _raw_gettext(string):
        t = get_translations()
        return t.gettext(string) if t else string

    def _raw_ngettext(singular, plural, num):
        t = get_translations()
        return t.ngettext(singular, plural, num) if t else (singular if num == 1 else plural)

    app.jinja_env.install_gettext_callables(
        gettext=_raw_gettext, ngettext=_raw_ngettext, newstyle=False
    )

    # Import models so SQLAlchemy/Alembic see every table.
    from app import models  # noqa: F401

    # ── Blueprints ────────────────────────────────────────────────────────
    from app.api import api_bp
    from app.auth.routes import auth_bp
    from app.owner import owner_bp
    from app.web import web_bp
    from app.web.routes_public import public_bp

    from app.addin import addin_bp
    from app.docs import docs_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(web_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(docs_bp)
    app.register_blueprint(addin_bp)
    # (Webhook endpoints exempt themselves from CSRF in app/api/webhooks.py; the rest
    #  of the API is CSRF-protected via the X-CSRFToken header sent by the JS layer.)

    # NB: there is deliberately NO cross-tenant data console. The instance administrator
    # manages organisations via the curated /owner backoffice, which exposes no tenant
    # mailbox content — so no one, administrator included, can browse another company's
    # data. See app/owner/ and the tenant-isolation tests.

    # ── Jinja globals & filters ───────────────────────────────────────────
    from app.web.filters import register_filters

    register_filters(app)

    # ── Per-request basics ────────────────────────────────────────────────
    from flask import g

    @app.before_request
    def bind_request():
        from secrets import token_urlsafe
        from uuid import uuid4

        from app.request_logging import start_timer

        start_timer()
        g.request_id = uuid4().hex[:12]
        # One fresh nonce per request — reusing one across responses would defeat it.
        g.csp_nonce = token_urlsafe(16)

    @app.context_processor
    def inject_globals():
        from flask import request, session
        from flask_babel import get_locale
        from flask_login import current_user

        from app.connectors import gmail_configured_for, graph_configured_for
        from app.extensions import LANGUAGE_NAMES
        from app.site import app_url, docs_url
        from app.web.context import js_i18n

        cfg = app.config
        current_locale = str(get_locale() or cfg["BABEL_DEFAULT_LOCALE"])
        org = current_user.organization if getattr(current_user, "is_authenticated", False) else None
        # Active platform-owner impersonation (drives the global banner in base.html).
        impersonating = None
        if session.get("impersonator_id") and getattr(current_user, "is_authenticated", False):
            impersonating = {"org": org.name if org else "", "user": getattr(current_user, "name", "")}
        return {
            "csp_nonce": getattr(g, "csp_nonce", ""),
            "impersonating": impersonating,
            "current_locale": current_locale,
            "languages": cfg["LANGUAGES"],
            "language_names": LANGUAGE_NAMES,
            # Client-side dialog strings only ship on the app + owner screens that
            # actually run them (otto.js/modals.js) — not on auth/docs pages.
            "otto_js_i18n": js_i18n() if getattr(request, "blueprint", None) in ("web", "owner") else {},
            "app_url": app_url,
            "docs_url": docs_url,
            "capabilities": {
                "gmail": gmail_configured_for(org),   # per-company: own app or platform
                "graph": graph_configured_for(org),
                "llm_stub": cfg.get("LLM_IS_STUB", True),
                "demo_mode": cfg.get("DEMO_MODE", True),
                "eager": cfg.get("TASK_ALWAYS_EAGER", True),
            },
        }

    # ── CLI ───────────────────────────────────────────────────────────────
    from app.cli import register_cli

    register_cli(app)

    # ── Background mailbox sync ───────────────────────────────────────────
    # Polls connected mailboxes on each account's configured interval. Self-gates:
    # only runs when there is no Celery beat (eager deployments) and never in tests.
    # Started on the first served request (idempotent) so it never spawns in the
    # Werkzeug reloader's parent process.
    from app.tasks.scheduler import start_background_sync

    @app.before_request
    def _ensure_background_sync():
        start_background_sync(app)

    # ── Error handlers ────────────────────────────────────────────────────
    # record_error persists a 5xx (with full traceback) to the owner debug console on an
    # INDEPENDENT db session, so a poisoned request transaction (the usual cause of a 500)
    # can't swallow the very error it caused. See app/request_logging.py.
    from app.errors import render_error
    from app.request_logging import log_request, record_error

    def _owner_diagnostics(exc) -> dict | None:
        """Exception details to show on a backoffice error page, or None.

        Only ever returned for the authenticated platform owner on a /owner page: the
        backoffice is a single-operator tool, so surfacing the exception there turns a
        dead end into something actionable. Customers never see any of it.
        """
        try:
            from flask import request

            from app.security.rbac import is_platform_owner

            if (request.blueprint or "") != "owner" or not is_platform_owner():
                return None
            import traceback as tb

            return {
                "type": type(exc).__name__ if exc is not None else "?",
                "message": str(exc) if exc is not None else "",
                "trace": "".join(tb.format_exception(type(exc), exc, exc.__traceback__))[:8000]
                if exc is not None else "",
            }
        except Exception:  # noqa: BLE001 — diagnostics must never break the error page
            return None

    def _error_page(code: int, exc=None):
        """One entry point for every error response, so the backoffice and the customer
        app can differ without either one being able to raise."""
        if code >= 500:
            diag = _owner_diagnostics(exc)
            if diag is not None:
                return render_error(code, template="errors/owner_500.html", diagnostics=diag)
        return render_error(code)

    @app.errorhandler(403)
    def forbidden(e):
        return _error_page(403)

    @app.errorhandler(404)
    def not_found(e):
        return _error_page(404)

    @app.errorhandler(429)
    def too_many(e):
        return _error_page(429)

    @app.errorhandler(500)
    def server_error(e):  # pragma: no cover - defensive
        return _error_page(500)

    @app.errorhandler(Exception)
    def unhandled(e):
        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            return e  # 4xx etc. keep their own handlers
        record_error(e, 500)
        app.logger.exception("unhandled exception")
        return _error_page(500, exc=e)

    def _db_ok() -> bool:
        from sqlalchemy import text

        try:
            db.session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False

    @app.route("/healthz")
    def healthz():
        """LIVENESS — 'is this process able to serve?'. Deliberately 200 even when the
        database is unreachable.

        This is the container healthcheck. It used to return 503 on any database
        error, which meant a brief Postgres hiccup marked the container unhealthy, the
        reverse proxy dropped its only upstream, and every visitor got the proxy's bare
        "no available server" screen — a full outage from a fault the app had already
        survived, on pages that need no database at all. Readiness now lives on /readyz,
        which is for monitoring and never gates traffic.
        """
        return {"status": "ok", "db": "ok" if _db_ok() else "error"}, 200

    @app.route("/readyz")
    def readyz():
        """READINESS — 503 when a dependency is down. For monitoring/alerting only;
        never wire this to the container healthcheck (see /healthz)."""
        ok = _db_ok()
        return {"status": "ok" if ok else "degraded", "db": "ok" if ok else "error"}, (200 if ok else 503)

    @app.after_request
    def security_headers(resp):
        from flask import request

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Het Outlook-taakvenster wordt door Outlook geframed en laadt office.js van de
        # Microsoft-CDN: geen X-Frame-Options (die kent geen host-allowlist) en een eigen CSP
        # met frame-ancestors voor de Outlook-hosts. De rest houdt de strakke policy.
        if (request.endpoint or "").startswith("addin."):
            resp.headers.setdefault("Content-Security-Policy", _addin_csp(getattr(g, "csp_nonce", "")))
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Content-Security-Policy", _csp(getattr(g, "csp_nonce", "")))
        if not app.debug:  # HSTS only off localhost (prod is always behind TLS)
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if getattr(g, "request_id", None):
            resp.headers.setdefault("X-Request-ID", g.request_id)
        return resp

    # Access log — one row per served request across every environment (owner Logboek).
    # Registered last so it runs after security_headers; uses its own db session.
    app.after_request(log_request)

    return app

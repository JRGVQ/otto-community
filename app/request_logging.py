"""Observability: the request access-log + bulletproof error capture.

Both writers use their OWN short-lived database session (a fresh connection from the
pool), never `db.session`. That matters: a 5xx is very often caused by a database error,
which leaves the request's session in an aborted transaction — trying to persist the log
on that same session would fail too, and the error would silently vanish (exactly the
"it's not in the debug console" symptom). An independent session sidesteps that entirely,
and also means the request log never accidentally commits a half-built view transaction.

What the Python layer cannot catch — a gunicorn worker timeout or a hard process kill —
leaves no traceback, but the access-log row written on `after_request` still records the
request that led up to it (method, path, org, duration), so even those are traceable.
"""
from __future__ import annotations

import time

from flask import g, request
from sqlalchemy.orm import Session

# Endpoints too noisy / irrelevant to record (health probes, static assets, favicon).
_SKIP_ENDPOINTS = {"static", "healthz", "readyz"}
_SKIP_PATHS = {"/favicon.ico", "/robots.txt"}


def _write_row(row) -> bool:
    """Persist one log row on an INDEPENDENT connection, immune to a poisoned request
    transaction (a fresh pool connection on Postgres). Falls back to the request session
    (after a rollback) when a separate connection isn't available — e.g. the single
    shared connection under SQLite/StaticPool in tests. Never raises."""
    from flask import current_app

    from app.extensions import db

    try:
        with Session(bind=db.engine) as s:
            s.add(row)
            s.commit()
        return True
    except Exception:  # noqa: BLE001 — fall back to the request session below
        pass
    try:
        db.session.rollback()  # clear any aborted transaction first
        db.session.add(row)
        db.session.commit()
        return True
    except Exception:  # noqa: BLE001
        try:
            db.session.rollback()
            current_app.logger.warning("log row write failed", exc_info=True)
        except Exception:  # noqa: BLE001
            pass
        return False


def _actor():
    """(user_email, org_name) for the current request — defensively, since the request's
    own session may already be broken when this runs after an error."""
    try:
        from flask_login import current_user

        if not getattr(current_user, "is_authenticated", False):
            return None, None
        email = getattr(current_user, "email", None)
        org = getattr(current_user, "organization", None)
        return email, (getattr(org, "name", None) if org else None)
    except Exception:  # noqa: BLE001
        return None, None


def start_timer() -> None:
    """Stamp the request start (call from a before_request hook)."""
    g._req_t0 = time.monotonic()


def _elapsed_ms() -> int | None:
    t0 = getattr(g, "_req_t0", None)
    return int((time.monotonic() - t0) * 1000) if t0 is not None else None


def redact_payloads(text: str | None) -> str:
    """Knip data-payloads uit een foutmelding/traceback.

    SQLAlchemy hangt bij een DBAPI-fout de volledige query én haar parameters aan de
    exception-tekst (`[SQL: ...] [parameters: ...]`). Bij Otto zit in die parameters
    klantmail: onderwerp, afzender, de volledige concepttekst. Die tekst wordt hier
    opgeslagen (leesbaar voor de platform-eigenaar, dus cross-tenant) en gaat bij een
    foutenpiek mee naar een GitHub-issue — mailinhoud hoort op geen van beide plekken.
    """
    if not text:
        return ""
    for marker in ("[SQL:", "[parameters:", "[generated in"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx].rstrip() + " […payload weggelaten…]"
    return text


def record_error(exc, status_code: int) -> None:
    """Persist a server-side error (5xx) with traceback to the owner debug console.

    Data-payloads worden eruit geknipt (zie `redact_payloads`), zodat er nooit klantmail
    in het foutenlog of in een alerting-issue belandt."""
    import traceback as tb

    from app.models import ErrorLog

    email, org_name = _actor()
    raw_tb = "".join(tb.format_exception(type(exc), exc, exc.__traceback__))
    row = ErrorLog(
        status_code=status_code,
        method=getattr(request, "method", None),
        path=getattr(request, "path", None),
        endpoint=getattr(request, "endpoint", None),
        exception_type=type(exc).__name__,
        message=redact_payloads(str(exc)) or type(exc).__name__,
        traceback=redact_payloads(raw_tb)[:8000],
        user_email=email,
        org_name=org_name,
        request_id=getattr(g, "request_id", None),
    )
    _write_row(row)


def log_request(response):
    """Record one access-log row (call from an after_request hook). Returns the response
    unchanged. Best-effort and gated by REQUEST_LOG_ENABLED (off under testing)."""
    from flask import current_app

    try:
        if not current_app.config.get("REQUEST_LOG_ENABLED", not current_app.config.get("TESTING")):
            return response
        if request.endpoint in _SKIP_ENDPOINTS or request.path in _SKIP_PATHS:
            return response
        email, org_name = _actor()
        from app.models import RequestLog

        row = RequestLog(
            site=getattr(g, "site", None),
            method=request.method,
            path=request.path[:512],
            endpoint=request.endpoint,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(),
            org_name=org_name,
            user_email=email,
            ip=(request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:64],
            request_id=getattr(g, "request_id", None),
        )
        _write_row(row)
    except Exception:  # noqa: BLE001 — never let logging break the response
        try:
            current_app.logger.warning("request log skipped", exc_info=True)
        except Exception:  # noqa: BLE001
            pass
    return response

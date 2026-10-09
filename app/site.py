"""URL helpers shared by templates and e-mails.

Otto runs on a single host. `app_url()` builds an absolute link from PUBLIC_BASE_URL —
needed in e-mails (verification, password reset, invitations), which are read outside
the browser session and may be sent from a background task without a request.
"""
from __future__ import annotations

from flask import current_app, url_for


def app_url(endpoint: str | None = None, **values) -> str:
    """Absolute URL to an app endpoint (or the root), based on PUBLIC_BASE_URL."""
    path = url_for(endpoint, **values) if endpoint else "/"
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}{path}" if base else path


def docs_url(path: str = "") -> str:
    """Link to a page of the in-app documentation."""
    return "/docs/" + path.lstrip("/") if path else "/docs/"

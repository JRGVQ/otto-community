"""Authenticated app blueprint (Postvak `/app/*` + Beheer `/admin/*`)."""
from __future__ import annotations

from flask import Blueprint, redirect, request, url_for
from flask_login import current_user

from app.security.rbac import is_platform_owner

web_bp = Blueprint("web", __name__)

# Endpoints a suspended/archived org may still reach — the blocked page itself — so it
# is never redirect-looped.
_LICENSE_EXEMPT = {"web.license_blocked"}

# Setup screens a NOT-yet-onboarded owner may reach, so they can wire up their own AI
# key + own mailbox app before connecting (see the onboarding flow). Everything else
# still funnels back to the onboarding page.
_ONBOARDING_SETUP = {"web.connector_settings", "web.admin_ai_byo"}


def _is_onboarding_setup() -> bool:
    ep = request.endpoint
    if ep in _ONBOARDING_SETUP:
        return True
    return ep == "web.admin_screen" and (request.view_args or {}).get("screen") == "ai"


@web_bp.before_request
def require_login_and_onboarding():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not current_user.onboarded and not _is_onboarding_setup():
        return redirect(url_for("auth.onboarding"))
    # The platform owner always passes (support/impersonation need full access).
    if is_platform_owner(current_user):
        return None

    org = current_user.organization
    if request.endpoint in _LICENSE_EXEMPT:
        return None
    # A soft-deleted (archived) environment or one the administrator suspended: keep the
    # user signed in and show the blocked page, which explains the situation.
    if org is not None and org.deleted_at is not None:
        return redirect(url_for("web.license_blocked"))
    lic = org.license if org else None
    if lic is None or lic.is_blocked:
        return redirect(url_for("web.license_blocked"))
    return None


# Import route modules so their handlers register on web_bp.
from app.web import routes_admin, routes_user  # noqa: E402,F401

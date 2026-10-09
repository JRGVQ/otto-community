"""Instance-administrator backoffice (/owner) — manages every organisation."""
from __future__ import annotations

from flask import Blueprint, abort, redirect, url_for
from flask_login import current_user

from app.security.rbac import is_platform_owner

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")


@owner_bp.before_request
def _guard():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not is_platform_owner(current_user):  # flag (AND PLATFORM_OWNER_EMAIL, when set)
        abort(403)
    return None


from app.owner import routes  # noqa: E402,F401

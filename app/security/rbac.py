"""Role-based access control."""
from __future__ import annotations

from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def is_platform_owner(user=None) -> bool:
    """True only for THE platform owner: the is_platform_owner flag AND — when a
    PLATFORM_OWNER_EMAIL is configured (production) — a matching e-mail. So a stray
    flag on any other account grants no platform access at all."""
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_platform_owner", False):
        return False
    allowed = current_app.config.get("PLATFORM_OWNER_EMAIL")
    return (not allowed) or (user.email or "").strip().lower() == allowed.strip().lower()


def require_platform_owner(view):
    """Abort 403 unless the current user is the platform owner (super-admin)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_platform_owner():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def require_perm(perm_key: str):
    """Abort 403 unless the current user's role grants `perm_key`."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not current_user.has_perm(perm_key):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator

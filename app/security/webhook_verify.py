"""Webhook authenticity checks (Gmail Pub/Sub OIDC token, Graph clientState).

Both fail CLOSED: a push notification is acted on only when its provider signature
verifies. Anything unverifiable — missing config, missing library, expired token,
a stranger's project — is treated as untrusted and silently ignored.
"""
from __future__ import annotations

import hmac

from flask import current_app


def _bearer(request) -> str | None:
    """The bearer token from the Authorization header, if present."""
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def verify_gmail_push(request) -> bool:
    """True when this really is Google Pub/Sub pushing to *our* subscription.

    Pub/Sub signs each push with an OIDC token. Two checks, both required:

    1. the token's signature, issuer and audience — proves Google issued it for the
       endpoint we configured;
    2. the service-account e-mail inside it — proves it came from *our* Pub/Sub
       subscription. Without this second check the first one is nearly worthless:
       anyone with a Google Cloud project can mint a valid OIDC token for any
       audience, so signature-only verification would accept a stranger's push.
    """
    cfg = current_app.config
    audience = cfg.get("GMAIL_PUSH_AUDIENCE")
    expected_sa = cfg.get("GMAIL_PUSH_SA_EMAIL")
    if not (audience and expected_sa):
        return False  # realtime not configured → never trust a push

    token = _bearer(request)
    if not token:
        return False

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError:
        # google-auth is an optional dependency, like every real integration in Otto.
        # Without it we cannot check the signature, so we must not trust the push.
        current_app.logger.warning(
            "Gmail-push genegeerd: google-auth is niet geïnstalleerd, dus de "
            "handtekening kan niet gecontroleerd worden."
        )
        return False

    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience)
    except Exception as exc:  # noqa: BLE001 — bad signature/audience/expiry are all "no"
        current_app.logger.info("Gmail-push met ongeldig token geweigerd: %s", exc)
        return False

    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return False
    if not claims.get("email_verified"):
        return False
    # Constant-time compare so the check can't be probed character by character.
    return hmac.compare_digest(str(claims.get("email") or "").lower(), str(expected_sa).lower())


def verify_graph_notification(request, expected_client_state: str | None) -> bool:
    # Fail closed: with no configured client-state secret, trust nothing.
    if not expected_client_state:
        return False
    payload = request.get_json(silent=True) or {}
    items = payload.get("value", [])
    if not items:
        return False
    # Every notification must carry the exact shared secret (constant-time compare).
    return all(
        hmac.compare_digest(str(item.get("clientState") or ""), str(expected_client_state))
        for item in items
    )

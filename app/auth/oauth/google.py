"""Google OAuth (Gmail). Scope: gmail.modify only — never gmail.send.

Note: gmail.modify is a *restricted* scope; production use requires Google's
security assessment. During development, use a Testing OAuth consent screen with
allow-listed test users.
"""
from __future__ import annotations

from flask import current_app, flash, redirect, request, session, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.auth.oauth.state import finish_onboarding, post_connect_target, redirect_uri
from app.connectors import gmail_configured_for, google_app
from app.providers.gmail import GMAIL_SCOPES


class GoogleSdkMissing(RuntimeError):
    """google-auth-oauthlib isn't installed, so no OAuth request can be built."""


def _flow():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:  # optional dependency — see requirements-prod.txt
        raise GoogleSdkMissing(str(exc)) from exc

    client_id, client_secret = google_app(current_user.organization)
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri("auth.oauth_google_callback"),
    )


def start_google_oauth():
    if not gmail_configured_for(current_user.organization):
        flash(_("De Gmail-koppeling is nog niet geconfigureerd voor jouw bedrijf."), "info")
        return redirect(url_for("auth.onboarding"))
    # Credentials set but the libraries missing is a deployment mistake, not a visitor's
    # problem: an unhandled ImportError here would be a 500 on the Verbinden button.
    try:
        flow = _flow()
    except GoogleSdkMissing as exc:
        current_app.logger.error(
            "Gmail is geconfigureerd maar de Google-bibliotheken ontbreken (%s) — "
            "installeer requirements-prod.txt op deze omgeving.", exc)
        flash(_("De Gmail-koppeling is op deze omgeving nog niet volledig geïnstalleerd. "
                "We zijn verwittigd."), "error")
        return redirect(url_for("auth.onboarding"))
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    session["oauth_state_google"] = state
    return redirect(auth_url)


def handle_google_callback():
    try:
        flow = _flow()
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        email = _userinfo_email(creds)
        finish_onboarding("gmail", email, creds.token, creds.refresh_token)
        flash(_("Gmail gekoppeld — Otto leert je mailbox kennen."), "toast")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Google OAuth failed: %s", exc)
        flash(_("Koppelen met Google is mislukt. Probeer opnieuw."), "error")
        return redirect(url_for("auth.onboarding"))
    return redirect(post_connect_target())


def _userinfo_email(creds) -> str:
    from googleapiclient.discovery import build

    profile = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "onbekend@gmail.com")

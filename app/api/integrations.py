"""Integration endpoints."""
from __future__ import annotations

from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import fail, ok

# Integrations that ride on the mailbox Graph token (see web/context.py).
LIVE_KEYS = {"outlook", "todo"}


def _ms_account():
    """The user's own connected Microsoft 365 mailbox, if any."""
    return next(
        (a for a in current_user.mail_accounts
         if a.provider == "outlook" and a.status == "connected" and not a.is_shared),
        None,
    )


@api_bp.route("/integrations/<key>/toggle", methods=["POST"])
@login_required
def toggle_integration(key: str):
    """Tiles are informational: live integrations follow the mailbox connection,
    the rest aren't built yet — never fake a 'connected' state either way."""
    if key in LIVE_KEYS:
        connected = _ms_account() is not None
        msg = ("Deze integratie loopt mee met je Microsoft 365-koppeling." if connected
               else "Koppel eerst je Microsoft 365-postvak — deze integratie loopt daarop mee.")
        return ok(connected=connected, toast=msg)
    return ok(connected=False, toast="Deze integratie is binnenkort beschikbaar.")


@api_bp.route("/integrations/crm/<provider>/test", methods=["POST"])
@login_required
def test_crm(provider: str):
    """Live probe van een gekoppeld CRM (app/crm/): echt authenticeren en lezen bij de
    provider, en de echte fout terugmelden — zelfde filosofie als de To Do-test."""
    from flask import abort
    from flask_login import current_user as user

    from app.crm import PROVIDERS, get_connector
    from app.extensions import db
    from app.models import CrmConnection

    meta = PROVIDERS.get(provider)
    if meta is None:
        abort(404)
    if not user.has_perm("manageProviders"):
        return fail("Alleen een beheerder kan koppelingen testen.", 403)
    conn = (
        db.session.query(CrmConnection)
        .filter_by(organization_id=user.organization_id, provider=provider)
        .first()
    )
    if conn is None or not conn.is_ready:
        return fail("Vul eerst de koppeling volledig in en schakel ze in.", 400)
    result = get_connector(conn).test_connection()
    if result.get("ok"):
        return ok(connection_ok=True, toast=result.get("detail") or f"{meta['name']} verbonden.")
    detail = result.get("detail") or "onbekende fout"
    return ok(connection_ok=False, error=detail,
              toast=f"{meta['name']}-verbinding mislukt — {detail}")


@api_bp.route("/integrations/todo/test", methods=["POST"])
@login_required
def test_todo():
    """Live probe of the Microsoft To Do connection: actually list the user's To Do
    lists over Graph and report the real result — or the real error — so 'fouten in
    de integraties' are visible and actionable instead of a blind 'OK'."""
    from app.providers.todo import TodoProvider
    from app.tasks.sync import describe_error

    account = _ms_account()
    if account is None:
        return ok(connection_ok=False, error="Koppel eerst je Microsoft 365-postvak."), 400
    cfg = account.provider_config or {}
    if cfg.get("demo") or cfg.get("seed"):
        return ok(connection_ok=True, toast="Demo-omgeving — To Do-koppeling gesimuleerd.")
    provider = TodoProvider(account)
    if not provider.is_configured():
        return ok(connection_ok=False,
                  error="Microsoft Graph is niet geconfigureerd op deze omgeving."), 400
    try:
        lists = provider.list_lists()
    except Exception as exc:  # noqa: BLE001 — surface the provider's reason to the admin
        detail = describe_error(exc)
        return ok(connection_ok=False, error=detail,
                  toast=f"To Do-verbinding mislukt — {detail}")
    n = len(lists)
    return ok(connection_ok=True,
              toast=f"To Do verbonden — {n} {'lijst' if n == 1 else 'lijsten'} gevonden.")

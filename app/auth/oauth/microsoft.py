"""Microsoft 365 / Graph OAuth (MSAL). Scope: Mail.ReadWrite — never Mail.Send.

Uses the organisation's own app when configured (BYO), else the platform app.
"""
from __future__ import annotations

from flask import current_app, flash, redirect, request, session, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.auth.oauth.state import finish_onboarding, post_connect_target, redirect_uri
from app.connectors import graph_configured_for, microsoft_app
from app.providers.outlook import GRAPH_MSAL_ADMIN_SCOPES, GRAPH_MSAL_SCOPES


def _msal_app(org):
    from msal import ConfidentialClientApplication

    client_id, tenant_id, secret = microsoft_app(org)
    return ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=secret,
    )


def start_microsoft_oauth(admin_consent: bool = False, connect_mailbox: bool = True):
    """Begin the Microsoft 365 OAuth connect.

    With ``admin_consent=True`` a tenant admin grants **organisation-wide** consent AND
    connects in one go: we add ``prompt=consent`` to the same authorization-code flow, so
    it reuses the already-registered connect redirect URI — no extra Azure config. An
    admin then sees the consent dialog with "Consent on behalf of your organization"; and
    because ``User.ReadBasic.All`` *requires* admin consent, granting it as an admin is
    tenant-wide by definition. After the admin approves once, every other user connects
    with no further prompt — clearing the "Goedkeuring van beheerder vereist" wall.

    With ``connect_mailbox=False`` (only meaningful together with ``admin_consent``) the
    admin ONLY approves: the callback records the org-wide consent maar koppelt het
    postvak van de beheerder NIET. Voor het geval de goedkeurder (bv. de IT-admin) zelf
    geen Otto-postvak wil — de echte gebruiker koppelt daarna zijn eigen postvak.

    NB: the value is ``consent`` — ``admin_consent`` is a legacy v1.0 value that the v2.0
    endpoint rejects with AADSTS901001; the dedicated org-wide flow is `prompt=consent`
    by an admin (per Entra's "perform admin consent" guidance).
    """
    org = current_user.organization
    if not graph_configured_for(org):
        flash(_("De Microsoft-koppeling is nog niet geconfigureerd voor jouw bedrijf."), "info")
        return redirect(url_for("auth.onboarding"))
    if admin_consent and not current_user.has_perm("manageProviders"):
        flash(_("Alleen een beheerder kan Otto goedkeuren voor de organisatie."), "error")
        return redirect(url_for("web.integrations"))

    extra = {}
    if admin_consent:
        extra["prompt"] = "consent"  # v2.0-valid; an admin's grant of an admin-only scope is tenant-wide
        session["ms_admin_consent_pending"] = True
        session["ms_admin_consent_connect"] = bool(connect_mailbox)
    else:
        session.pop("ms_admin_consent_pending", None)  # don't let a stale flag leak in
        session.pop("ms_admin_consent_connect", None)
    # Normal connect requests only user-consentable scopes (no admin wall). The admin-consent
    # flow additionally asks for the directory scope so an admin grants it org-wide.
    scopes = GRAPH_MSAL_ADMIN_SCOPES if admin_consent else GRAPH_MSAL_SCOPES
    auth_url = _msal_app(org).get_authorization_request_url(
        scopes,
        redirect_uri=redirect_uri("auth.oauth_microsoft_callback"),
        **extra,
    )
    return redirect(auth_url)


def handle_microsoft_callback():
    # Set by start_microsoft_oauth(admin_consent=True); a successful return then means the
    # admin granted organisation-wide consent, so record it for the ✓ state.
    admin_consent = session.pop("ms_admin_consent_pending", False)
    # False = "alleen goedkeuren": consent vastleggen zonder het postvak van de
    # beheerder te koppelen (de echte gebruiker koppelt daarna zijn eigen postvak).
    connect_mailbox = session.pop("ms_admin_consent_connect", True) or not admin_consent
    code = request.args.get("code")
    if not code:
        # Microsoft redirected back with an error instead of a code (consent denied,
        # admin-approval required, redirect-URI mismatch, …). Surface the real reason
        # instead of a vague "cancelled", and log it so it lands in the backoffice Logboek.
        err = request.args.get("error_description") or request.args.get("error")
        if err:
            current_app.logger.warning("Microsoft OAuth denied: %s", err)
            flash(_("Koppelen met Microsoft is geweigerd: %(reason)s", reason=str(err)[:300]), "error")
        else:
            flash(_("Koppelen met Microsoft is afgebroken."), "error")
        return redirect(url_for("auth.onboarding"))
    try:
        result = _msal_app(current_user.organization).acquire_token_by_authorization_code(
            code,
            scopes=GRAPH_MSAL_SCOPES,
            redirect_uri=redirect_uri("auth.oauth_microsoft_callback"),
        )
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description", "geen token"))
        email = (result.get("id_token_claims") or {}).get("preferred_username", "onbekend@outlook.com")
        if connect_mailbox:
            finish_onboarding("outlook", email, result["access_token"], result.get("refresh_token"),
                              expires_in=result.get("expires_in"))
            _enrich_profile_from_graph(current_user, result["access_token"])
        if admin_consent:
            _record_admin_consent(current_user.organization)
            if connect_mailbox:
                flash(_("Microsoft 365 gekoppeld én goedgekeurd voor je hele organisatie — "
                        "collega's koppelen nu zonder los toestemmingsscherm."), "toast")
            else:
                # Het token wordt bewust weggegooid: er is geen postvak om het aan te hangen.
                flash(_("Otto is goedgekeurd voor je hele organisatie — jouw postvak is NIET "
                        "gekoppeld. Gebruikers koppelen nu elk hun eigen postvak in één klik."),
                      "toast")
        else:
            flash(_("Outlook gekoppeld — Otto leert je mailbox kennen."), "toast")
    except Exception as exc:  # noqa: BLE001
        from app.extensions import db

        db.session.rollback()  # clear any half-failed transaction so nothing cascades
        current_app.logger.warning("Microsoft OAuth failed: %s", exc)
        flash(_("Koppelen met Microsoft is mislukt: %(reason)s", reason=str(exc)[:300]), "error")
        return redirect(url_for("auth.onboarding"))
    if not connect_mailbox:
        # Alleen goedgekeurd — er is geen vers gekoppeld postvak om naartoe te springen.
        return redirect(url_for("web.integrations"))
    return redirect(post_connect_target())


def _enrich_profile_from_graph(user, access_token: str) -> None:
    """Fill profile blanks from the Microsoft 365 profile — het hele profiel: naam,
    functie, afdeling, locatie, vaste lijn én gsm. Ze voeden o.a. de handtekening-
    placeholders ({{functie}}, {{afdeling}}, {{telefoon}}, {{mobiel}}, {{locatie}}).

    Best-effort — never blocks the connect. Only fills what's empty; the invite
    placeholder name (derived from the e-mail prefix) counts as empty, but a name
    the user typed themselves is never overwritten."""
    import json
    import urllib.request

    from app.extensions import db
    from app.models.user import initials_for

    from app.graph_profile import PROFILE_SELECT, fill_profile_blanks

    try:
        url = f"https://graph.microsoft.com/v1.0/me?$select={PROFILE_SELECT}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted Graph host)
            me = json.loads(resp.read().decode() or "{}")
        changed = False
        placeholder = (user.email or "").split("@")[0].replace(".", " ").title()
        display = (me.get("displayName") or "").strip()
        if display and (not user.name or user.name.strip() in ("", placeholder)):
            user.name = display[:128]
            user.initials = initials_for(user.name)
            changed = True
        # Alleen lege velden invullen — wat de gebruiker zelf typte blijft staan.
        changed = fill_profile_blanks(user, me) or changed
        if changed:
            db.session.commit()
    except Exception as exc:  # noqa: BLE001 — verrijking mag het koppelen nooit breken
        db.session.rollback()  # anders blijft een gefaalde commit als PendingRollbackError hangen
        current_app.logger.info("Graph profile enrichment skipped: %s", exc)


def _record_admin_consent(org) -> None:
    """Stamp that an admin granted organisation-wide consent (drives the ✓ approved state
    on Integraties + the backoffice E-mailkoppeling screen)."""
    if org is None:
        return
    from app.extensions import db
    from app.models.base import utcnow

    org.graph_admin_consented_at = utcnow()
    db.session.commit()

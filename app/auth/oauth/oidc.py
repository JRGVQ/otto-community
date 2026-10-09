"""Per-company login SSO via OIDC (Microsoft Entra / Google / Okta).

Authorization-code flow. The user's e-mail is read from the IdP's userinfo
endpoint — both the code→token exchange and the userinfo call are direct
back-channel calls over TLS, so we don't hand-validate id_token signatures.
A successful login provisions the user just-in-time into the matching org.
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
import urllib.request

from flask import current_app, flash, redirect, request, session, url_for
from flask_babel import gettext as _
from flask_login import login_user

from app.auth.oauth.state import redirect_uri
from app.extensions import db


# ── HTTP helpers (back-channel, trusted IdP host) ────────────────────────────
def _discover(issuer: str) -> dict:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode({k: v for k, v in data.items() if v is not None}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _get_bearer(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


# ── Resolution + provisioning ────────────────────────────────────────────────
def sso_usable(sso) -> bool:
    """Whether this SSO config can actually complete a login — OIDC or SAML.

    Both protocols count: an organisation that only set up SAML must still be found by
    e-mail domain, otherwise its people would silently fall back to password login.
    """
    return bool(sso and sso.enabled and (sso.oidc_ready or sso.saml_ready))


def sso_org_for_email(email: str):
    """The organisation whose enabled SSO covers this e-mail's domain, if any.

    `allowed_domains` is free text, so on its own it proves nothing: any tenant could
    list a domain it does not own and capture that company's SSO logins. A *verified*
    domain (see app/domains.py) is therefore exclusive — it resolves only to the org
    that proved control, and no other org's allowed_domains can override it. Domains
    nobody verified keep matching as before.
    """
    from app.domains import domain_of, verified_owner
    from app.models import SsoConfig

    domain = domain_of(email)
    if not domain:
        return None

    owner = verified_owner(domain)
    if owner is not None:
        return owner if sso_usable(owner.sso) else None

    for sso in db.session.query(SsoConfig).filter_by(enabled=True):
        if not sso_usable(sso):
            continue
        domains = [d.lower() for d in (sso.allowed_domains or [])]
        if sso.domain:
            domains.append(sso.domain.lower())
        if domain in domains:
            return sso.organization
    return None


def _find_or_provision(org, email: str, name: str):
    """Existing user (same org) or a just-in-time one; None if not allowed."""
    from app.models import Role, User
    from app.models.user import initials_for

    user = db.session.query(User).filter_by(email=email).first()
    if user:
        if user.organization_id != org.id:
            return None  # never move across orgs
        if user.status == "Geblokkeerd":
            return None  # blocked stays blocked, also via the IdP
        if user.status == "Uitgenodigd":
            # An invited teammate signing in through the company IdP: that proves
            # control of the invited address, so activate the account on the spot.
            user.status = "Actief"
            user.email_verified = True
            db.session.commit()
        return user
    if org.sso and not org.sso.jit:
        return None
    role = db.session.query(Role).filter_by(organization_id=org.id, name="Gebruiker").first()
    user = User(
        email=email, name=name, initials=initials_for(name), avatar_color="#6C4CE6",
        organization_id=org.id, role_id=role.id if role else None, status="Actief",
        last_active="nu", mailbox_count=0, onboarded=True, email_verified=True,
    )
    user.set_password(secrets.token_urlsafe(24))  # random; SSO users never sign in with it
    db.session.add(user)
    db.session.commit()
    return user


# ── Flow ─────────────────────────────────────────────────────────────────────
def start_sso(org):
    sso = org.sso
    meta = _discover(sso.oidc_issuer)
    state = secrets.token_urlsafe(24)
    session["sso_state"] = state
    session["sso_org_id"] = org.id
    params = urllib.parse.urlencode({
        "client_id": sso.oidc_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri("auth.sso_callback"),
        "state": state,
    })
    return redirect(meta["authorization_endpoint"] + "?" + params)


def handle_sso_callback():
    from app.models import Organization
    from app.security.crypto import decrypt

    if not request.args.get("state") or request.args.get("state") != session.pop("sso_state", None):
        flash(_("SSO-sessie verlopen of ongeldig. Probeer opnieuw."), "error")
        return redirect(url_for("auth.login"))
    org = db.session.get(Organization, session.pop("sso_org_id", 0) or 0)
    code = request.args.get("code")
    if not org or not org.sso or not code:
        flash(_("Aanmelden met SSO is afgebroken."), "error")
        return redirect(url_for("auth.login"))
    try:
        meta = _discover(org.sso.oidc_issuer)
        token = _post_form(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri("auth.sso_callback"),
            "client_id": org.sso.oidc_client_id,
            "client_secret": decrypt(org.sso.oidc_client_secret_enc),
        })
        info = _get_bearer(meta["userinfo_endpoint"], token["access_token"])
        email = (info.get("email") or info.get("preferred_username") or "").lower().strip()
        name = info.get("name") or (email.split("@")[0] if email else "Gebruiker")
        if not email:
            raise RuntimeError("geen e-mail ontvangen van de IdP")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("SSO callback failed: %s", exc)
        flash(_("Aanmelden met SSO is mislukt. Probeer opnieuw."), "error")
        return redirect(url_for("auth.login"))

    user = _find_or_provision(org, email, name)
    if user is None:
        flash(_("SSO-aanmelding geweigerd (geen toegang voor dit account)."), "error")
        return redirect(url_for("auth.login"))
    if user.status == "Geblokkeerd":
        flash(_("Dit account is geblokkeerd."), "error")
        return redirect(url_for("auth.login"))
    user.touch()
    db.session.commit()
    login_user(user)
    return redirect(url_for("web.inbox") if user.onboarded else url_for("auth.onboarding"))

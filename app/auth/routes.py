"""Registration, login, logout and the onboarding/scan flow."""
from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from urllib.parse import urlparse

from app.auth.forms import (
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from app.auth.service import create_company, email_exists, email_in_trashed_org
from app.domains import normalize_email
from app.extensions import csrf, db, limiter
from app.models import MailAccount, User

auth_bp = Blueprint("auth", __name__)

PROVIDERS = [
    {"key": "gmail", "name": "Gmail", "tag": "G", "color": "#EA4335", "desc": "Google Workspace · via OAuth"},
    {"key": "outlook", "name": "Microsoft 365 / Outlook", "tag": "O", "color": "#0078D4", "desc": "Microsoft Graph · via OAuth"},
    {"key": "imap", "name": "Ander (IMAP / SMTP)", "tag": "@", "color": "#6C4CE6", "desc": "Generiek postvak · app-wachtwoord"},
]


def _safe_next(target: str | None) -> str | None:
    """Only allow same-app relative redirects — never an absolute or off-host URL
    (prevents open-redirect via ?next=https://evil or //evil)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _surface_verification(link: str | None) -> None:
    """Decide what to do when no e-mail was transmitted.

    Test environments (SHOW_VERIFY_LINK) reveal the link on-screen so signups are
    testable without SMTP. Production never leaks it — a non-send there means SMTP
    is misconfigured, which is an admin problem we log (visible in the server logs
    / Sentry), not something to show the visitor.
    """
    if not link:
        return  # a real verification e-mail was sent
    if current_app.config.get("SHOW_VERIFY_LINK"):
        flash(_("Test-modus (geen SMTP): open de verificatielink → %(link)s", link=link), "info")
    else:
        current_app.logger.error("Verification e-mail could not be sent — configure SMTP on this environment.")


def _surface_reset(link: str | None) -> None:
    """Same contract as _surface_verification, for the password-reset link.

    Test environments reveal the link on-screen (no SMTP needed); production never
    leaks it — a non-send there is a misconfigured SMTP, logged for the admin.
    """
    if not link:
        return  # a real reset e-mail was sent
    if current_app.config.get("SHOW_VERIFY_LINK"):
        flash(_("Test-modus (geen SMTP): open de reset-link → %(link)s", link=link), "info")
    else:
        current_app.logger.error("Password-reset e-mail could not be sent — configure SMTP on this environment.")


# ── First-run setup ──────────────────────────────────────────────────────────
def needs_setup() -> bool:
    """True on a brand-new installation: no account exists yet. The very first visitor
    then creates the first organisation + the instance administrator via /auth/setup."""
    return db.session.query(User.id).first() is None


@auth_bp.route("/setup", methods=["GET", "POST"])
@limiter.limit("8 per minute; 40 per hour", methods=["POST"])
def setup():
    """Create the first organisation and its owner, who also becomes the administrator
    of this Otto installation (the /owner backoffice). Only reachable while the database
    holds no users at all — afterwards it redirects to the login page."""
    from app.auth.forms import SetupForm

    if not needs_setup():
        return redirect(url_for("auth.login"))
    form = SetupForm()
    if form.validate_on_submit():
        try:
            user = create_company(form.company.data, form.name.data, form.email.data, form.password.data)
            user.email_verified = True  # nobody to verify against yet; the admin owns this box
            user.is_platform_owner = True
            db.session.commit()
        except Exception:  # noqa: BLE001 — provisioning is one atomic transaction
            db.session.rollback()
            current_app.logger.exception("First-run setup failed for %s", form.email.data)
            flash(_("Er ging iets mis bij het aanmaken van je omgeving. Probeer het opnieuw."), "error")
            return render_template("auth/setup.html", form=form), 500
        login_user(user)
        flash(_("Je installatie is klaar. Koppel nu je mailbox."), "info")
        return redirect(url_for("auth.onboarding"))
    return render_template("auth/setup.html", form=form)


# ── Register ─────────────────────────────────────────────────────────────────
@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("8 per minute; 40 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("web.inbox"))
    if needs_setup():
        return redirect(url_for("auth.setup"))
    # Self-registration is OFF by default: on a self-hosted instance the administrator
    # creates organisations in /owner and owners invite their colleagues. Set
    # REGISTRATION_OPEN=1 to let anyone create their own organisation.
    if not current_app.config.get("REGISTRATION_OPEN"):
        return render_template("auth/register_closed.html"), 403
    form = RegisterForm()
    if form.validate_on_submit():
        # A verified domain belongs to exactly one organisation: route colleagues to that
        # environment instead of silently splitting a company over two tenants.
        from app.domains import registration_blocked_by

        if (owner_org := registration_blocked_by(form.email.data)) is not None:
            form.email.errors.append(_(
                "Het domein van dit e-mailadres is al geverifieerd door %(company)s. "
                "Vraag daar een uitnodiging aan, of meld je aan met je bedrijfsaccount.",
                company=owner_org.name,
            ))
            return render_template("auth/register.html", form=form)
        if not email_exists(form.email.data):
            from app.email import send_verification

            try:
                user = create_company(form.company.data, form.name.data, form.email.data, form.password.data)
            except Exception:  # noqa: BLE001 — provisioning is one atomic transaction
                # Creating the org + owner failed part-way (a DB error, an oversized field,
                # a slug race that survived its retry). Roll the half-built transaction back
                # so the session is clean, log it for the owner console, and hand the visitor
                # their form back with a clear message instead of a dead-end 500. Nothing was
                # committed, so the address stays free to try again.
                db.session.rollback()
                current_app.logger.exception("Registration failed for %s", form.email.data)
                flash(_(
                    "Er ging iets mis bij het aanmaken van je omgeving. Probeer het opnieuw — "
                    "lukt het daarna nog niet, neem dan contact op met de beheerder."
                ), "error")
                return render_template("auth/register.html", form=form), 500
            # The account now exists and is committed. Everything below is best-effort
            # side-work — a hiccup in any of it must NEVER turn a successful signup into a 500.
            org = user.organization
            try:
                from app.email import notify_platform_owner
                notify_platform_owner(
                    f"Nieuwe Otto-omgeving: {org.name}",
                    f"<p><strong>{org.name}</strong> is aangemaakt door "
                    f"{user.name} &lt;{user.email}&gt; — omgeving <code>{org.slug}</code>.</p>",
                )
            except Exception:  # noqa: BLE001 - the ops notice must never block signup
                current_app.logger.warning("Admin signup notice failed (org %s).", org.id, exc_info=True)
            try:
                _surface_verification(send_verification(user))  # not logged in until verified
            except Exception:  # noqa: BLE001 - a mailer failure must not lose the account
                current_app.logger.exception("Verification e-mail failed at signup (org %s).", org.id)
        elif email_in_trashed_org(form.email.data):
            # The address belongs to a soft-deleted environment: pretending a mail went
            # out just confuses (the org owner especially). Say so, actionably. This only
            # reveals trashed orgs — active accounts still get the neutral response below,
            # so real accounts stay non-enumerable.
            form.email.errors.append(_(
                "Dit e-mailadres hoort bij een omgeving die verwijderd is en nog in de "
                "prullenbak zit. Herstel die omgeving of verwijder ze definitief voor je "
                "met dit adres opnieuw registreert."
            ))
            return render_template("auth/register.html", form=form)
        # Neutral response for a free address or an active account — never reveal which.
        return render_template("auth/verify_sent.html", email=form.email.data)
    return render_template("auth/register.html", form=form)


@auth_bp.route("/verify/<token>")
def verify_email(token: str):
    from itsdangerous import BadSignature, SignatureExpired

    from app.email import read_verify_token

    try:
        email = read_verify_token(token)
    except SignatureExpired:
        return render_template("auth/verify_result.html", state="expired")
    except BadSignature:
        return render_template("auth/verify_result.html", state="invalid")

    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        return render_template("auth/verify_result.html", state="invalid")
    if not user.email_verified:
        user.email_verified = True
        db.session.commit()
    login_user(user)
    return redirect(url_for("auth.onboarding") if not user.onboarded else url_for("web.inbox"))


@auth_bp.route("/resend", methods=["POST"])
@limiter.limit("4 per minute; 15 per hour")
def resend():
    from app.email import send_verification

    email = normalize_email(request.form.get("email"))
    user = db.session.query(User).filter_by(email=email).first()
    if user and not user.email_verified:
        _surface_verification(send_verification(user))
    flash(_("Als dit adres bestaat, hebben we een nieuwe link verstuurd."), "info")
    return render_template("auth/verify_sent.html", email=email)


# ── Login ────────────────────────────────────────────────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 60 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("web.inbox"))
    if needs_setup():
        return redirect(url_for("auth.setup"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            if user.status == "Geblokkeerd":
                flash(_("Dit account is geblokkeerd."), "error")
            elif not user.email_verified:
                return render_template("auth/verify_sent.html", email=user.email, unverified=True)
            else:
                user.touch()
                db.session.commit()
                login_user(user, remember=form.remember.data)
                if not user.onboarded:
                    return redirect(url_for("auth.onboarding"))
                return redirect(_safe_next(request.args.get("next")) or url_for("web.inbox"))
        else:
            flash(_("Onjuist e-mailadres of wachtwoord."), "error")
    return render_template("auth/login.html", form=form)


# ── Password reset (self-serve) ───────────────────────────────────────────────
@auth_bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def forgot_password():
    """Step 1: ask for an e-mail and send a reset link if the account exists."""
    if current_user.is_authenticated:
        return redirect(url_for("web.inbox"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        from app.email import send_password_reset

        email = form.email.data.lower().strip()
        user = db.session.query(User).filter_by(email=email).first()
        if user:
            _surface_reset(send_password_reset(user))
        # Always the same neutral response — never reveal whether an account exists.
        return render_template("auth/forgot_sent.html", email=email)
    return render_template("auth/forgot.html", form=form)


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def reset_password(token: str):
    """Step 2: validate the signed link and let the user choose a new password."""
    from itsdangerous import BadSignature, SignatureExpired

    from app.email import read_reset_token

    try:
        email = read_reset_token(token)
    except SignatureExpired:
        return render_template("auth/reset_result.html", state="expired")
    except BadSignature:
        return render_template("auth/reset_result.html", state="invalid")

    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        return render_template("auth/reset_result.html", state="invalid")

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.email_verified = True  # a successful reset also proves control of the inbox
        db.session.commit()
        flash(_("Je wachtwoord is opnieuw ingesteld — meld je aan met je nieuwe wachtwoord."), "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", form=form, token=token)


@auth_bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def accept_invite(token: str):
    """Invited teammate lands here: choose a name + password, or continue with the
    company's SSO. The signed link proves control of the invited inbox."""
    from itsdangerous import BadSignature, SignatureExpired

    from app.auth.forms import AcceptInviteForm
    from app.email import read_invite_token
    from app.models.user import initials_for

    try:
        email = read_invite_token(token)
    except SignatureExpired:
        flash(_("Deze uitnodigingslink is verlopen — vraag je beheerder om een nieuwe."), "error")
        return redirect(url_for("auth.login"))
    except BadSignature:
        flash(_("Ongeldige uitnodigingslink."), "error")
        return redirect(url_for("auth.login"))

    user = db.session.query(User).filter_by(email=email).first()
    if user is None or user.status != "Uitgenodigd":
        flash(_("Deze uitnodiging is niet meer geldig — meld je gewoon aan, of vraag je "
                "beheerder om een nieuwe uitnodiging."), "info")
        return redirect(url_for("auth.login"))
    if current_user.is_authenticated:
        logout_user()  # accepting an invite always starts a fresh session

    org = user.organization
    sso_available = bool(org and org.sso and org.sso.enabled and org.sso.oidc_issuer)
    form = AcceptInviteForm(name=user.name)
    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.initials = initials_for(user.name)
        user.set_password(form.password.data)
        user.status = "Actief"
        user.email_verified = True  # the mailed link proves control of the inbox
        user.touch()
        from app.events import record_event

        record_event(org, "member.joined", detail=user.email, actor=user.name)
        db.session.commit()
        login_user(user)
        return redirect(url_for("auth.onboarding"))
    return render_template("auth/invite.html", form=form, token=token, user=user,
                           org=org, sso_available=sso_available)


@auth_bp.route("/sso", methods=["GET", "POST"])
def sso_login():
    """Bedrijfs-SSO: werk-e-mail invullen → door naar de IdP van die organisatie.

    Otto spreekt zowel OIDC als SAML 2.0. Welke van de twee gebruikt wordt volgt uit wat
    de organisatie heeft ingesteld; heeft ze allebei, dan wint de expliciete keuze in
    `method`. Zo hoeft de gebruiker niets te weten over het protocol.
    """
    if current_user.is_authenticated:
        return redirect(url_for("web.inbox"))
    if request.method == "POST":
        from app.auth.oauth.oidc import sso_org_for_email, start_sso
        from app.auth.oauth.saml import start_saml

        email = normalize_email(request.form.get("email"))
        org = sso_org_for_email(email)
        if org is not None:
            sso = org.sso
            prefers_saml = (sso.method or "").upper().startswith("SAML")
            if sso.saml_ready and (prefers_saml or not sso.oidc_ready):
                return start_saml(org)
            if sso.oidc_ready:
                return start_sso(org)
        flash(_("Geen SSO gevonden voor dit e-maildomein. Meld je aan met e-mail en wachtwoord."), "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/sso_start.html")


@auth_bp.route("/sso/callback")
def sso_callback():
    from app.auth.oauth.oidc import handle_sso_callback

    return handle_sso_callback()


@auth_bp.route("/saml/acs", methods=["POST"])
@csrf.exempt
def saml_acs():
    """Assertion Consumer Service — hier post de IdP zijn antwoord.

    CSRF-vrijgesteld omdat het bericht van de IdP komt en niet van een formulier van ons.
    De bescherming zit elders: de handtekening moet kloppen, en de assertie moet via
    InResponseTo teruggekoppeld zijn aan het verzoek dat in déze sessie is gestart.
    """
    from app.auth.oauth.saml import handle_saml_acs

    return handle_saml_acs()


@auth_bp.route("/saml/metadata")
def saml_metadata():
    """SP-metadata, zodat een beheerder Otto in één keer in zijn IdP kan zetten."""
    from flask import Response

    from app.auth.oauth.saml import sp_metadata

    return Response(sp_metadata(), mimetype="application/samlmetadata+xml")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash(_("Je bent afgemeld."), "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/stop-impersonation", methods=["POST"])
def stop_impersonation():
    """End a platform-owner impersonation session and restore the owner account.
    Reachable while impersonating (the active user is then a normal company user), and
    it only ever restores the stored owner id — never an arbitrary user."""
    owner_id = session.pop("impersonator_id", None)
    if owner_id:
        owner = db.session.get(User, owner_id)
        if owner and owner.is_platform_owner:
            login_user(owner)
            flash(_("Impersonatie beëindigd."), "info")
            return redirect(url_for("owner.dashboard"))
    return redirect(url_for("web.inbox"))


# ── Onboarding ───────────────────────────────────────────────────────────────
def _after_onboarding() -> str:
    """Where to send the owner once onboarding is done: the one-time intro the first
    time, the inbox afterwards."""
    return url_for("web.rondleiding") if not current_user.intro_seen else url_for("web.inbox")


@auth_bp.route("/onboarding")
@login_required
def onboarding():
    if current_user.onboarded:
        return redirect(url_for("web.inbox"))
    from app.connectors import (
        gmail_configured_for,
        graph_configured_for,
        org_uses_own_google,
        org_uses_own_microsoft,
    )
    from app.ai.models import template_context as byo_model_context
    from app.web.context import BYO_PROVIDER_GUIDES, BYO_PROVIDERS

    org = current_user.organization
    ai = getattr(org, "ai", None)
    cfg = current_app.config
    # No instance-wide AI model configured → offer the owner to add their own key first.
    is_own_key = bool(cfg.get("LLM_IS_STUB", True)) and current_user.has_perm("manageAI")
    return render_template(
        "auth/onboarding.html", providers=PROVIDERS,
        is_own_key=is_own_key,
        needs_ai_key=is_own_key and not (ai and ai.byo_api_key_enc),
        graph_ready=graph_configured_for(org), gmail_ready=gmail_configured_for(org),
        # Whether the SHARED platform app is configured (one-click connect via consent,
        # no per-customer app). Distinct from the org's own BYO app.
        platform_graph=bool(cfg.get("GRAPH_CONFIGURED")),
        platform_gmail=bool(cfg.get("GMAIL_CONFIGURED")),
        # An owner can grant org-wide admin consent (for tenants that block user consent).
        can_admin_consent=current_user.has_perm("manageProviders"),
        uses_own_ms=org_uses_own_microsoft(org), uses_own_google=org_uses_own_google(org),
        byo_providers=BYO_PROVIDERS,
        byo_guides=BYO_PROVIDER_GUIDES,
        ai_provider=(ai.byo_provider if ai else None),
        ai_model=(ai.byo_model if ai else None),
        ai_base_url=(ai.byo_base_url if ai else None),
        byo_has_key=bool(ai and ai.byo_api_key_enc),
        # Same model picker as the AI settings screen (see app/ai/models.py).
        **byo_model_context(ai),
    )


@auth_bp.route("/onboarding/connect", methods=["POST"])
@login_required
def onboarding_connect():
    provider = request.form.get("provider", "outlook")
    session["onboarding_provider"] = provider

    # Real OAuth when the provider is configured — either the org's own (BYO) app or
    # the shared platform app. graph/gmail_configured_for honours both.
    from app.connectors import gmail_configured_for, graph_configured_for

    org = current_user.organization
    if provider == "gmail" and gmail_configured_for(org):
        return redirect(url_for("auth.oauth_google"))
    if provider == "outlook" and graph_configured_for(org):
        return redirect(url_for("auth.oauth_microsoft"))

    # No instance-wide app AND no own app yet. Don't dead-end — take the owner straight
    # to the "own mailbox app" screen (with a return path back to onboarding), so they can
    # register their own Microsoft/Google app and then connect.
    if provider in ("outlook", "gmail"):
        flash(_("Stel eerst je eigen %(prov)s-app in, dan koppel je je mailbox.",
                prov=("Microsoft" if provider == "outlook" else "Google")), "info")
        return redirect(url_for("web.connector_settings", from_onboarding=1))
    # IMAP/other has no self-service app screen yet — keep the honest fallback.
    flash(_("Deze koppeling is nog niet ingesteld. Vraag je beheerder om de mailbox-app te "
            "configureren, of kies een andere provider."), "info")
    return redirect(url_for("auth.onboarding"))


@auth_bp.route("/onboarding/complete", methods=["POST"])
@login_required
def onboarding_complete():
    provider = session.pop("onboarding_provider", "outlook")
    if not current_user.mail_accounts:
        account = MailAccount(
            user_id=current_user.id, provider=provider, email=current_user.email,
            status="connected", last_sync="zojuist", provider_config={"demo": True},
        )
        db.session.add(account)
        current_user.mailbox_count = 1
    current_user.onboarded = True
    db.session.commit()
    flash(_("Otto is gekoppeld — welkom!"), "toast")
    return redirect(_after_onboarding())


@auth_bp.route("/onboarding/finish", methods=["POST"])
@login_required
def onboarding_finish():
    """Enter the (empty) app and finish setup from inside — no mailbox required yet."""
    current_user.onboarded = True
    db.session.commit()
    return redirect(_after_onboarding())


@auth_bp.route("/onboarding/sample", methods=["POST"])
@login_required
def onboarding_sample():
    """Optional: load a tiny sample inbox so the owner can explore before connecting."""
    from app.models import Message
    from app.tenancy import add_welcome_inbox

    org = current_user.organization
    has_messages = db.session.query(Message.id).filter_by(organization_id=org.id).first() if org else None
    if org and not has_messages:
        add_welcome_inbox(org, current_user)
    current_user.onboarded = True
    db.session.commit()
    flash(_("Voorbeelddata geladen — verken Otto vrijuit."), "toast")
    return redirect(_after_onboarding())


# ── OAuth (real flows; gracefully degrade when not configured) ───────────────
@auth_bp.route("/google")
@login_required
def oauth_google():
    from app.auth.oauth.google import start_google_oauth

    return start_google_oauth()


@auth_bp.route("/google/callback")
@login_required
def oauth_google_callback():
    from app.auth.oauth.google import handle_google_callback

    return handle_google_callback()


@auth_bp.route("/microsoft")
@login_required
def oauth_microsoft():
    from app.auth.oauth.microsoft import start_microsoft_oauth

    return start_microsoft_oauth()


@auth_bp.route("/microsoft/callback")
@login_required
def oauth_microsoft_callback():
    from app.auth.oauth.microsoft import handle_microsoft_callback

    return handle_microsoft_callback()


@auth_bp.route("/microsoft/admin-consent")
@login_required
def oauth_microsoft_admin_consent():
    # Admin connects AND grants organisation-wide consent in one automatic flow
    # (prompt=consent on the normal connect → reuses the registered redirect URI).
    # Met ?koppel=0 keurt de beheerder ALLEEN goed: de consent wordt vastgelegd maar
    # zijn eigen postvak wordt niet gekoppeld (voor een IT-admin zonder Otto-postvak).
    from app.auth.oauth.microsoft import start_microsoft_oauth

    connect_mailbox = request.args.get("koppel", "1") != "0"
    return start_microsoft_oauth(admin_consent=True, connect_mailbox=connect_mailbox)

"""Admin (Beheer) endpoints — each gated by the relevant permission."""
from __future__ import annotations

from flask import current_app, request
from flask_babel import gettext as _
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import ok
from app.extensions import db
from app.models import (
    AiConfig,
    GraphScope,
    Organization,
    Permission,
    Role,
    RolePermission,
    SsoConfig,
    User,
)
from app.security.rbac import require_perm


def _payload():
    return request.get_json(silent=True) or request.form


def _org() -> Organization:
    return db.session.get(Organization, current_user.organization_id)


# ── Users & roles ────────────────────────────────────────────────────────────
@api_bp.route("/admin/users/invite", methods=["POST"])
@login_required
@require_perm("manageUsers")
def invite_user():
    """Invite a teammate: an e-mail address is enough. They pick their own name and
    password on the accept page (or the details come along via Microsoft/SSO login)."""
    from app.constants import AVATAR_COLORS
    from app.email import send_invite
    from app.models import AuditLog
    from app.models.user import initials_for

    data = _payload()
    from app.domains import normalize_email

    email = normalize_email(data.get("email"))
    name = (data.get("name") or "").strip() or email.split("@")[0].replace(".", " ").title()
    role_name = (data.get("role") or "Gebruiker").strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        return ok(error="Geef een geldig e-mailadres op."), 400
    if db.session.query(User).filter_by(email=email).first():
        return ok(error="Er bestaat al een gebruiker met dit e-mailadres."), 400

    org_id = current_user.organization_id
    # Roles are per organisation — scope the lookup so a same-named role from another
    # tenant can never be attached here.
    role = db.session.query(Role).filter_by(organization_id=org_id, name=role_name).first()
    if role and role.is_owner:
        return ok(error="De rol Eigenaar kan hier niet worden toegekend."), 403

    count = db.session.query(User).filter_by(organization_id=org_id).count()
    user = User(
        email=email, name=name, organization_id=org_id,
        role_id=role.id if role else None, status="Uitgenodigd",
        initials=initials_for(name), avatar_color=AVATAR_COLORS[count % len(AVATAR_COLORS)],
    )
    db.session.add(user)
    db.session.add(AuditLog(
        organization_id=org_id, actor=(current_user.name or current_user.email),
        action=f"nodigde {email} uit ({role_name})", kind="user",
    ))
    from app.events import record_event

    record_event(current_user.organization, "member.invited", detail=f"{email} ({role_name})",
                 actor=(current_user.name or current_user.email))
    db.session.commit()
    link = send_invite(user, current_user)
    # send_invite returns a link only when no mailer is configured (dev). Surface it just
    # in the test/dev env — production never leaks it (and there the mail is actually sent).
    if link and current_app.config.get("SHOW_VERIFY_LINK"):
        return ok(toast="Uitnodiging aangemaakt (test-modus).", link=link, reload=True)
    return ok(toast=f"Uitnodiging gemaild naar {email}.", reload=True)


@api_bp.route("/admin/users/<int:user_id>/invite/resend", methods=["POST"])
@login_required
@require_perm("manageUsers")
def resend_invite(user_id: int):
    from app.email import send_invite

    user = db.session.get(User, user_id)
    if not user or user.organization_id != current_user.organization_id:
        return ok(error="onbekend"), 404
    if user.status != "Uitgenodigd":
        return ok(error="Deze gebruiker is al actief."), 400
    link = send_invite(user, current_user)
    if link and current_app.config.get("SHOW_VERIFY_LINK"):
        return ok(toast="Nieuwe uitnodigingslink (test-modus).", link=link)
    return ok(toast=f"Uitnodiging opnieuw gemaild naar {user.email}.")


@api_bp.route("/admin/users/<int:user_id>/invite/revoke", methods=["POST"])
@login_required
@require_perm("manageUsers")
def revoke_invite(user_id: int):
    """Withdraw a pending invite — removes the placeholder user so the seat frees up
    and the mailed link stops working (accept requires status 'Uitgenodigd')."""
    from app.models import AuditLog

    user = db.session.get(User, user_id)
    if not user or user.organization_id != current_user.organization_id:
        return ok(error="onbekend"), 404
    if user.status != "Uitgenodigd":
        return ok(error="Alleen openstaande uitnodigingen kunnen worden ingetrokken."), 400
    email = user.email
    db.session.delete(user)
    db.session.add(AuditLog(
        organization_id=current_user.organization_id,
        actor=(current_user.name or current_user.email),
        action=f"trok de uitnodiging voor {email} in", kind="user",
    ))
    db.session.commit()
    return ok(toast=f"Uitnodiging voor {email} ingetrokken.", reload=True)


@api_bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
@require_perm("manageUsers")
def change_role(user_id: int):
    user = db.session.get(User, user_id)
    if not user or user.organization_id != current_user.organization_id:
        return ok(error="onbekend"), 404
    role = db.session.query(Role).filter_by(organization_id=current_user.organization_id, name=_payload().get("role")).first()
    if not role:
        return ok(error="onbekende rol"), 400
    # Never grant the owner role here: it would let any manageUsers holder (e.g. a
    # Beheerder) self-promote to full owner. Ownership transfer is a separate,
    # deliberate action — mirror the owner-role guard already in toggle_perm.
    if role.is_owner:
        return ok(error="De rol Eigenaar kan hier niet worden toegekend"), 403
    user.role_id = role.id
    db.session.commit()
    return ok(toast="Rol bijgewerkt")


@api_bp.route("/admin/perms/toggle", methods=["POST"])
@login_required
@require_perm("manageUsers")
def toggle_perm():
    data = _payload()
    role = db.session.query(Role).filter_by(organization_id=current_user.organization_id, name=data.get("role")).first()
    perm = db.session.query(Permission).filter_by(key=data.get("perm")).first()
    if not (role and perm):
        return ok(error="onbekend"), 404
    if role.is_owner:
        return ok(locked=True, toast="De rol Eigenaar heeft altijd alle rechten")
    rp = db.session.query(RolePermission).filter_by(role_id=role.id, permission_id=perm.id).first()
    if rp:
        rp.allowed = not rp.allowed
        db.session.commit()
        return ok(on=rp.allowed)
    return ok(error="onbekend"), 404


# ── Providers & Graph ────────────────────────────────────────────────────────
@api_bp.route("/admin/scopes/<name>/toggle", methods=["POST"])
@login_required
@require_perm("manageProviders")
def toggle_scope(name: str):
    scope = (
        db.session.query(GraphScope)
        .filter_by(organization_id=current_user.organization_id, name=name)
        .first()
    )
    if not scope:
        return ok(error="onbekend"), 404
    if scope.locked:
        return ok(on=scope.on, locked=True)
    scope.on = not scope.on
    db.session.commit()
    return ok(on=scope.on)


@api_bp.route("/admin/test", methods=["POST"])
@login_required
def test_connection():
    label = _payload().get("label", "Verbinding")
    return ok(toast=f"{label}: verbinding OK")


# ── SSO ──────────────────────────────────────────────────────────────────────
@api_bp.route("/admin/sso/toggle", methods=["POST"])
@login_required
@require_perm("manageSSO")
def toggle_sso():
    key = _payload().get("key")
    sso: SsoConfig = _org().sso
    if key in {"enabled", "enforce", "mfa", "jit"}:
        setattr(sso, key, not getattr(sso, key))
        db.session.commit()
        return ok(on=getattr(sso, key))
    return ok(error="onbekend"), 400


@api_bp.route("/admin/sso/method", methods=["POST"])
@login_required
@require_perm("manageSSO")
def set_sso_method():
    _org().sso.method = _payload().get("method", "SAML 2.0")
    db.session.commit()
    return ok()


# ── AI & model ───────────────────────────────────────────────────────────────
@api_bp.route("/admin/ai/model", methods=["POST"])
@login_required
@require_perm("manageAI")
def set_model():
    _org().ai.model = _payload().get("model", "azure")
    db.session.commit()
    return ok()


@api_bp.route("/admin/ai/toggle", methods=["POST"])
@login_required
@require_perm("manageAI")
def toggle_ai():
    key = _payload().get("key")
    ai: AiConfig = _org().ai
    if key in {"never_train", "fewshot"}:
        setattr(ai, key, not getattr(ai, key))
        db.session.commit()
        return ok(on=getattr(ai, key))
    if key == "allow_training":
        # Opt-in framing: ON = training allowed = never_train False. Default off.
        ai.never_train = not ai.never_train
        db.session.commit()
        return ok(on=not ai.never_train)
    return ok(error="onbekend"), 400


@api_bp.route("/admin/ai/value", methods=["POST"])
@login_required
@require_perm("manageAI")
def set_ai_value():
    data = _payload()
    key, value = data.get("key"), data.get("value")
    ai: AiConfig = _org().ai
    if key in {"limit", "confidence"}:
        try:
            setattr(ai, key, int(value))
            db.session.commit()
        except (TypeError, ValueError):
            return ok(error="ongeldige waarde"), 400
        return ok(value=getattr(ai, key))
    return ok(error="onbekend"), 400


# ── Outlook-categorienummering ───────────────────────────────────────────────
@api_bp.route("/admin/outlook-numbering/toggle", methods=["POST"])
@login_required
@require_perm("manageOrgCats")
def toggle_outlook_numbering():
    """Zet nummering van de categorieën in Outlook aan of uit.

    Het omzetten moet METEEN zichtbaar zijn, anders lijkt de schakelaar stuk: per
    gekoppelde mailbox wordt de nieuwe naamvorm direct in Outlooks categorielijst
    gezet (met kleur) en de niet meer gebruikte vorm eruit gehaald, zodat er niet
    twee naast elkaar blijven staan. Bestaande mail wordt daarna HER-getagd van de
    oude vorm naar de nieuwe (app/tasks/retag.py): anders bleef het oude label —
    grijs, want uit de lijst verwijderd — naast het nieuwe staan en droeg mail twee
    categorieën. Alles best-effort: mislukt een stap, dan zet de eerstvolgende
    gelabelde mail hem alsnog recht.
    """
    from app.models import MailAccount
    from app.outlook_labels import all_labels, label_for, stale_variants
    from app.tasks.retag import retag_org_labels

    org = _org()
    org.outlook_category_numbering = not org.outlook_category_numbering
    db.session.commit()

    obsolete = all_labels(org, numbered=not org.outlook_category_numbering)
    wanted = [(label_for(cat, org), cat.color)
              for cat in sorted(org.categories, key=lambda c: c.position or 0)]
    if obsolete or wanted:
        from app.providers import get_provider

        accounts = db.session.query(MailAccount).filter_by(
            organization_id=org.id, provider="outlook", status="connected"
        ).all()
        for account in accounts:
            try:
                provider = get_provider(account)
                if not provider.is_configured():
                    continue
                if hasattr(provider, "sync_master_categories"):
                    # Eén lijst-GET + alleen de nodige POSTs/DELETEs — geen rondreis
                    # per naam (remove_master_category doet anders per naam een
                    # volledige lijst-GET), dus geen minutenlang hangend request.
                    provider.sync_master_categories(wanted, obsolete)
                    continue
                # Terugval voor providers zonder bulk-methode: eerst de nieuwe vorm
                # erin, dan pas de oude eruit — nooit een moment met een lege lijst.
                if hasattr(provider, "ensure_master_category"):
                    for name, color in wanted:
                        provider.ensure_master_category(name, color)
                if hasattr(provider, "remove_master_category"):
                    for name in obsolete:
                        provider.remove_master_category(name)
            except Exception:  # noqa: BLE001 — bijwerken mag de schakelaar nooit blokkeren
                current_app.logger.info("Categorienamen bijwerken overgeslagen voor %s", account.email)

    # Bestaande mail meeverhuizen naar de nieuwe naamvorm (per mailbox, best-effort).
    retag_org_labels(org, stale_variants(org))

    toast = (_("Categorieën krijgen voortaan een nummer in Outlook — bestaande mail wordt mee omgezet.")
             if org.outlook_category_numbering else _("Nummering in Outlook uitgezet — bestaande mail wordt mee omgezet."))
    return ok(on=org.outlook_category_numbering, toast=toast)


@api_bp.route("/admin/outlook-labels/realign", methods=["POST"])
@login_required
@require_perm("manageOrgCats")
def realign_outlook_labels():
    """Ruim de Outlook-labels van alle gekoppelde mailboxen op.

    Herschrijft elke verouderde labelvorm (ongenummerd of met een oud nummer) naar
    de huidige juiste naam en haalt dubbele Otto-categorieën van berichten af. De
    reddingsknop voor mailboxen die vóór de her-tag-fix een nummering-switch of
    hernoeming meemaakten en met twee tags per mail achterbleven."""
    from app.outlook_labels import stale_variants
    from app.tasks.retag import retag_org_labels

    org = _org()
    retag_org_labels(org, stale_variants(org))
    return ok(toast=_("Outlook-labels worden opgeruimd — bestaande mail krijgt weer precies één categorie."))


# ── Org category push ────────────────────────────────────────────────────────
@api_bp.route("/admin/orgpush/toggle", methods=["POST"])
@login_required
@require_perm("manageOrgCats")
def toggle_orgpush():
    org = _org()
    org.org_push_cats = not org.org_push_cats
    db.session.commit()
    return ok(on=org.org_push_cats)

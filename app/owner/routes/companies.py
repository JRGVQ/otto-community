"""Bedrijven: dashboard, lijst, detail, status (actief/geschorst) en aanmaken."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_user

from app.extensions import db
from app.models import AuditLog, License, Organization
from app.models.base import utcnow
from app.owner import owner_bp
from app.owner.forms import CreateCompanyForm
from app.owner.routes._shared import _company_row, _ctx


# ── Dashboard ────────────────────────────────────────────────────────────────
@owner_bp.route("/")
def dashboard():
    from datetime import timedelta

    from app.models import ErrorLog, MailAccount, Message, User

    orgs = db.session.query(Organization).filter(Organization.deleted_at.is_(None)).order_by(Organization.id).all()
    rows = [_company_row(o) for o in orgs]
    suspended = sum(1 for r in rows if not (r["license"] and r["license"].is_active))
    day_ago = utcnow() - timedelta(hours=24)
    users = db.session.query(User).filter(User.status != "Geblokkeerd").count()
    mailboxes = db.session.query(MailAccount).count()
    mails_24h = db.session.query(Message).filter(Message.created_at >= day_ago).count()
    errors_24h = db.session.query(ErrorLog).filter(ErrorLog.occurred_at >= day_ago).count()

    stats = [
        {"value": str(len(rows)), "label": _("Organisaties"), "color": "#6C4CE6"},
        {"value": str(users), "label": _("Actieve gebruikers"), "color": "#2D9CDB"},
        {"value": str(mailboxes), "label": _("Gekoppelde postvakken"), "color": "#0078D4"},
        {"value": str(mails_24h), "label": _("Mails verwerkt (24 u)"), "color": "#27AE60"},
        {"value": str(errors_24h), "label": _("Serverfouten (24 u)"), "color": "#EB5757" if errors_24h else "#9AA4B2"},
        {"value": str(suspended), "label": _("Geschorste organisaties"), "color": "#F2994A"},
    ]
    return render_template("owner/dashboard.html", stats=stats, rows=rows, **_ctx("dashboard"))


# ── Companies list ───────────────────────────────────────────────────────────
@owner_bp.route("/companies")
def companies():
    active = db.session.query(Organization).filter(Organization.deleted_at.is_(None)).order_by(Organization.id).all()
    deleted = db.session.query(Organization).filter(Organization.deleted_at.isnot(None)).order_by(Organization.id).all()
    return render_template("owner/companies.html", rows=[_company_row(o) for o in active],
                           trash=[_company_row(o) for o in deleted], **_ctx("companies"))


@owner_bp.route("/companies/<int:org_id>/delete", methods=["POST"])
def delete_company(org_id: int):
    """Soft-delete: move an environment to the trash (recoverable)."""
    from app.tenancy import soft_delete_org

    from app.events import record_event

    org = db.session.get(Organization, org_id)
    if org:
        record_event(org, "org.deleted", detail="via backoffice", actor="beheerder")
        soft_delete_org(org)  # commits, so the event lands with it
        flash(_("%(name)s naar de prullenbak verplaatst.", name=org.name), "toast")
    return redirect(url_for("owner.companies"))


@owner_bp.route("/companies/<int:org_id>/restore", methods=["POST"])
def restore_company(org_id: int):
    from app.tenancy import restore_org

    from app.events import record_event

    org = db.session.get(Organization, org_id)
    if org:
        record_event(org, "org.restored", actor="beheerder")
        restore_org(org)  # commits, so the event lands with it
        flash(_("%(name)s hersteld.", name=org.name), "toast")
    return redirect(url_for("owner.company_detail", org_id=org_id))


@owner_bp.route("/companies/<int:org_id>/destroy", methods=["POST"])
def destroy_company(org_id: int):
    """Permanent delete — only from the trash, so an active org can't be wiped by accident.
    Defence in depth: the typed confirmation must match the org's slug."""
    from app.tenancy import delete_org_deep

    org = db.session.get(Organization, org_id)
    if not org or not org.deleted_at:
        return redirect(url_for("owner.companies"))
    confirm = (request.form.get("confirm") or "").strip()
    if confirm != org.slug:
        flash(_("Bevestiging klopt niet — typ exact “%(slug)s” om %(name)s definitief te "
                "verwijderen.", slug=org.slug, name=org.name), "error")
        return redirect(url_for("owner.companies"))
    name = org.name
    delete_org_deep(org)
    db.session.commit()
    flash(_("%(name)s definitief verwijderd.", name=name), "toast")
    return redirect(url_for("owner.companies"))


# ── Company detail ───────────────────────────────────────────────────────────
@owner_bp.route("/companies/<int:org_id>")
def company_detail(org_id: int):
    org = db.session.get(Organization, org_id)
    if not org:
        return redirect(url_for("owner.companies"))
    from app.events import events_for, label_for

    return render_template(
        "owner/company_detail.html", row=_company_row(org), org=org,
        users=org.users, events=events_for(org, limit=25), label_for=label_for,
        **_ctx("companies"),
    )


@owner_bp.route("/companies/<int:org_id>/impersonate", methods=["POST"])
def impersonate(org_id: int):
    """Step into a company's environment as one of its users — instance administrator
    ONLY (this whole blueprint is gated on is_platform_owner). It's deliberate and audited;
    the impersonated session carries only that company's access (no platform-owner
    powers), and normal tenant isolation confines it to that one company. End it via
    the banner (auth.stop_impersonation)."""
    org = db.session.get(Organization, org_id)
    if not org:
        abort(404)
    # Impersonate the company's owner; never another platform owner.
    target = org.owner if (org.owner and not org.owner.is_platform_owner) else next(
        (u for u in org.users if not u.is_platform_owner and u.status != "Geblokkeerd"), None
    )
    if target is None:
        flash(_("Geen geschikte gebruiker om als in te loggen in dit bedrijf."), "error")
        return redirect(url_for("owner.company_detail", org_id=org.id))
    db.session.add(AuditLog(
        organization_id=org.id, actor="Beheerder",
        action=f"bekeek deze omgeving als {target.name} (impersonatie)",
        kind="security", occurred_at=utcnow(),
    ))
    from app.events import record_event

    record_event(org, "owner.impersonated", actor="beheerder")
    db.session.commit()
    session["impersonator_id"] = current_user.id  # remember who to restore
    login_user(target)
    flash(_("Je bekijkt nu %(org)s als %(target)s. Gebruik de banner om te stoppen.",
            org=org.name, target=target.name), "info")
    return redirect(url_for("web.inbox"))


@owner_bp.route("/companies/<int:org_id>/status", methods=["POST"])
def set_status(org_id: int):
    """Suspend or re-activate an organisation (everyone in it loses / regains access)."""
    org = db.session.get(Organization, org_id)
    if not org:
        return redirect(url_for("owner.companies"))
    lic = org.license
    if lic is None:
        lic = org.license = License.new_active()
    action = request.form.get("action")
    before = lic.status
    if action == "suspend":
        lic.status = "suspended"
        flash(_("%(name)s geschorst", name=org.name), "toast")
    elif action == "activate":
        lic.status = "active"
        flash(_("%(name)s geactiveerd", name=org.name), "toast")
    if before != lic.status:
        from app.events import record_event

        record_event(org, "license.status_changed", detail=f"{before} → {lic.status}", actor="beheerder")
    db.session.commit()
    return redirect(url_for("owner.company_detail", org_id=org.id))


# ── Create company ───────────────────────────────────────────────────────────
@owner_bp.route("/companies/new", methods=["GET", "POST"])
def create_company():
    form = CreateCompanyForm()
    if form.validate_on_submit():
        from app.auth.service import email_exists

        if email_exists(form.owner_email.data):
            flash(_("Er bestaat al een account met dit e-mailadres."), "error")
        else:
            from app.tenancy import add_owner_user, provision_organization

            domain = form.owner_email.data.split("@")[-1]
            org = provision_organization(form.company.data, domain=domain, demo=False)
            add_owner_user(org, form.owner_name.data, form.owner_email.data, form.password.data, verified=True)
            db.session.commit()
            flash(_("Bedrijf '%(name)s' aangemaakt", name=org.name), "toast")
            return redirect(url_for("owner.company_detail", org_id=org.id))
    return render_template("owner/create_company.html", form=form, **_ctx("companies"))



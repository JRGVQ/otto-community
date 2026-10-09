"""Postvak (user workspace) screens."""
from __future__ import annotations

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app import inbox_digest
from app.extensions import db
from app.security.rbac import require_perm
from app.web import context as ctx
from app.web import web_bp


def _is_owner(user, org) -> bool:
    return bool((user.role and user.role.is_owner) or (org and org.owner_user_id == user.id))


def _owner_or_403(user, org):
    if not _is_owner(user, org):
        abort(403)


def _render(screen: str, workspace: str = "user", active: str | None = None, **extra):
    # `active` lets a screen highlight a different sidebar item than its template
    # name (e.g. the Outlook view keeps the Inbox item active).
    data = ctx.base_context(workspace, active or screen)
    data.update(extra)
    return render_template(f"app/user/{screen}.html", **data)


def _mark_read(message_id: int | None):
    """Opening a message marks it read (the prototype's select())."""
    if not message_id:
        return
    from app.models import Message

    msg = db.session.get(Message, message_id)
    if not (msg and msg.unread and msg.visible_to(ctx.current_user)):
        return
    msg.unread = False
    db.session.commit()


@web_bp.route("/app")
def app_home():
    return redirect(url_for("web.inbox"))


@web_bp.route("/app/rondleiding")
def rondleiding():
    """Post-onboarding intro: a one-glance map of where everything lives. Shown once
    automatically right after onboarding; always reachable again from the sidebar."""
    user = ctx.current_user
    if not user.intro_seen:  # viewing it counts as seen (so it won't auto-open again)
        user.intro_seen = True
        db.session.commit()
    has_mailbox = bool(user.mail_accounts)
    return _render("rondleiding", active="rondleiding", has_mailbox=has_mailbox)


@web_bp.route("/licentie")
def license_blocked():
    """Shown when the administrator suspended this organisation, or it is in the trash."""
    user = ctx.current_user
    org = user.organization
    archived = bool(org and org.deleted_at is not None)
    return render_template("app/license_blocked.html", me=user, archived=archived)


@web_bp.route("/app/inbox")
def inbox():
    filter_key = request.args.get("cat", "alle")
    selected_id = request.args.get("m", type=int)
    search = (request.args.get("q") or "").strip()
    unread_only = request.args.get("unread") == "1"
    sort = "old" if request.args.get("sort") == "old" else "new"
    _mark_read(selected_id)
    data = ctx.inbox_context(
        filter_key, selected_id,
        search=search, unread_only=unread_only, sort=sort,
    )
    from app.foldering import folder_suggestion

    folders, suggested = folder_suggestion(data.get("selected"))
    return _render("inbox", **data, folders=folders, suggested_folder=suggested)


@web_bp.route("/app/messages/<int:message_id>/move", methods=["POST"])
@require_perm("editDrafts")
def move_message(message_id: int):
    """Move a message to one of its mailbox folders (auto-foldering one-click)."""
    from app.models import Message
    from app.providers import get_provider

    msg = db.session.get(Message, message_id)
    if not (msg and msg.visible_to(ctx.current_user)):
        abort(404)
    folder_id = (request.form.get("folder_id") or "").strip()
    if folder_id and msg.account and msg.provider_message_id:
        try:
            get_provider(msg.account).move_to_folder(msg.provider_message_id, folder_id)
            msg.archived = True
            db.session.commit()
            flash(_("Mail verplaatst naar de map."), "toast")
        except Exception:  # noqa: BLE001
            current_app.logger.exception("move_to_folder failed")
            db.session.rollback()
            flash(_("Verplaatsen mislukt — probeer opnieuw."), "error")
    return redirect(url_for("web.inbox"))


@web_bp.route("/app/messages/<int:message_id>/inline/<path:content_id>")
def message_inline_image(message_id: int, content_id: str):
    """Eén inline-afbeelding (cid:…) uit een bericht, on demand via de provider.

    De HTML-weergave herschrijft src="cid:…" naar deze route (app/htmlview.py), zodat
    inline plaatjes achter Otto's eigen login geserveerd worden en nooit in de database
    hoeven. Cache-headers houden herhaald scrollen door dezelfde thread goedkoop."""
    from flask import Response

    from app.models import Message
    from app.providers import get_provider

    msg = db.session.get(Message, message_id)
    if not (msg and msg.visible_to(ctx.current_user)):
        abort(404)
    if not (msg.account and msg.provider_message_id):
        abort(404)
    try:
        provider = get_provider(msg.account)
        found = provider.get_inline_attachment(msg.provider_message_id, content_id) if provider.is_configured() else None
    except Exception:  # noqa: BLE001 — een kapot plaatje is een 404, geen 500
        found = None
    if not found:
        abort(404)
    data, mimetype = found
    resp = Response(data, mimetype=mimetype)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@web_bp.route("/media/user-photo/<int:user_id>")
def signature_user_photo(user_id: int):
    """De gecachte M365-profielfoto van een collega — alleen voor de handtekening-preview.

    Strikt org-gescoped (geen IDOR tussen tenants). In de aflevering wordt deze route niet
    gebruikt: daar gaat de foto als cid:-bijlage mee (een geauthenticeerde Otto-URL werkt
    niet in het postvak van de ontvanger)."""
    from flask import Response

    from app.models import UserPhoto

    photo = db.session.get(UserPhoto, user_id)
    if not (photo and photo.data and photo.organization_id == ctx.current_user.organization_id):
        abort(404)
    resp = Response(photo.data, mimetype=photo.content_type or "image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@web_bp.route("/media/sig-asset/<int:asset_id>")
def signature_asset(asset_id: int):
    """Een org-logo/banner voor de handtekening-preview. Strikt org-gescoped; in de
    aflevering wordt de src naar cid: herschreven en gaan de bytes als bijlage mee."""
    from flask import Response

    from app.models import SignatureAsset

    asset = db.session.get(SignatureAsset, asset_id)
    if not (asset and asset.data and asset.organization_id == ctx.current_user.organization_id):
        abort(404)
    resp = Response(asset.data, mimetype=asset.content_type or "image/png")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@web_bp.route("/app/outlook")
def outlook():
    folder = request.args.get("folder", "inbox")
    selected_id = request.args.get("m", type=int)
    _mark_read(selected_id)
    # Outlook is a view of the inbox, not its own nav item — keep Inbox highlighted.
    return _render("outlook", active="inbox", **ctx.outlook_context(folder, selected_id))


@web_bp.route("/app/briefing")
def briefing():
    return _render("briefing", **ctx.briefing_context())


@web_bp.route("/app/tasks")
def tasks():
    list_id = request.args.get("list", type=int)
    return _render("tasks", **ctx.tasks_context(list_id))


@web_bp.route("/app/tasks/<int:task_id>/attachments/<int:att_id>")
def download_attachment(task_id: int, att_id: int):
    """Stream a To Do task attachment's bytes from Graph. Owner-scoped (tasks are personal)."""
    from flask import Response

    from app.models import Task, TaskAttachment
    from app.tasks import task_ops

    task = db.session.get(Task, task_id)
    if not task or task.user_id != ctx.current_user.id:
        abort(404)
    att = db.session.get(TaskAttachment, att_id)
    if not att or att.task_id != task.id:
        abort(404)
    data = task_ops.attachment_bytes(att)
    if data is None:
        abort(404)
    safe_name = (att.name or "bestand").replace('"', "")
    return Response(data, headers={
        "Content-Type": att.content_type or "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{safe_name}"',
    })


@web_bp.route("/app/analyse")
def analyse():
    # ?p= kiest de periode (7/30/90/alles); de widgets en hun volgorde komen uit
    # het register + de opgeslagen indeling van de gebruiker (ctx.analytics_page_context).
    return _render("analyse", **ctx.analytics_page_context(request.args.get("p")))


@web_bp.route("/app/teams")
def teams():
    return _render("teams", **ctx.teams_context())


@web_bp.route("/app/crm")
def crm():
    return _render("crm", active="crm", **ctx.crm_context())


@web_bp.route("/app/crm/projecten")
def crm_projects():
    return _render("crm_projects", active="crm", **ctx.projects_context())


@web_bp.route("/app/crm/projecten/<int:project_id>")
def crm_project(project_id: int):
    from app.models import Project

    project = db.session.get(Project, project_id)
    if not project or project.organization_id != ctx.current_user.organization_id:
        abort(404)
    return _render("crm_project", active="crm", **ctx.project_detail_context(project))


@web_bp.route("/app/teams/<int:account_id>")
def team_inbox(account_id: int):
    """One shared mailbox, consulted on its own — the inbox scoped to that mailbox."""
    acct = ctx._resolve_team_mailbox(account_id)
    if acct is None:
        abort(404)  # not shared, wrong org, not a member, or plan without Teams
    filter_key = request.args.get("cat", "alle")
    selected_id = request.args.get("m", type=int)
    search = (request.args.get("q") or "").strip()
    unread_only = request.args.get("unread") == "1"
    sort = "old" if request.args.get("sort") == "old" else "new"
    _mark_read(selected_id)
    data = ctx.team_inbox_context(
        acct, filter_key, selected_id,
        search=search, unread_only=unread_only, sort=sort,
    )
    from app.foldering import folder_suggestion

    folders, suggested = folder_suggestion(data.get("selected"))
    return _render("inbox", active="teams", **data, folders=folders, suggested_folder=suggested)


@web_bp.route("/app/teams/<int:account_id>/analyse")
def team_analytics(account_id: int):
    """Cijfers over één gedeelde mailbox — volume, doorlooptijd, SLA en werkverdeling."""
    acct = ctx._resolve_team_mailbox(account_id)
    if acct is None:
        abort(404)  # niet gedeeld, andere org, geen lid, of een plan zonder Teams
    return _render("team_analytics", active="teams", **ctx.team_analytics_context(acct))


@web_bp.route("/app/teams/<int:account_id>/settings", methods=["GET", "POST"])
def team_settings(account_id: int):
    acct = ctx._resolve_team_mailbox(account_id)
    if acct is None:
        abort(404)
    if request.method == "POST":
        if not ctx._can_manage_mailbox(acct):
            flash(_("Alleen een postvak-beheerder kan de instellingen wijzigen."), "info")
            return redirect(url_for("web.team_settings", account_id=account_id))
        _save_mailbox_settings(acct.settings, request.form)
        db.session.commit()
        flash(_("Postvak-instellingen opgeslagen."), "toast")
        return redirect(url_for("web.team_settings", account_id=account_id))
    return _render("team_detail", active="teams", **ctx.team_settings_context(acct))


def _save_mailbox_settings(s, form) -> None:
    """Apply the Teams settings form to a MailboxSettings row (no commit)."""
    from app.constants import COLOR_CHOICES, TONES

    s.display_name = (form.get("display_name") or "").strip() or None
    color = (form.get("color") or "").strip()
    if color in COLOR_CHOICES:
        s.color = color
    tone = (form.get("tone") or "").strip()
    if tone in TONES:
        s.tone = tone
    language = (form.get("language") or "").strip()
    if language:
        s.language = language
    s.signature = (form.get("signature") or "").strip() or None
    s.default_reply = (form.get("default_reply") or "").strip() or None
    s.auto_draft = form.get("auto_draft") == "on"
    sla = (form.get("sla_minutes") or "").strip()
    s.sla_minutes = int(sla) if sla.isdigit() else None
    s.teams_webhook_url = (form.get("teams_webhook_url") or "").strip() or None
    s.teams_notify = form.get("teams_notify") == "on"
    s.teams_bundle = form.get("teams_bundle") == "on"
    from app.teams_bundle import parse_hours as _tb_hours
    tbh = (form.get("teams_bundle_hours") or "").strip()
    s.teams_bundle_hours = ",".join(str(h) for h in _tb_hours(tbh)) if tbh else None
    # Automation prefs (mailbox-level mirror of the personal settings).
    s.follow_up_enabled = form.get("follow_up_enabled") == "on"
    fud = (form.get("follow_up_days") or "").strip()
    s.follow_up_days = int(fud) if fud.isdigit() and int(fud) > 0 else 3
    ttl = (form.get("draft_ttl_days") or "").strip()
    s.draft_ttl_days = int(ttl) if ttl.isdigit() and int(ttl) > 0 else None
    s.auto_folder_enabled = form.get("auto_folder_enabled") == "on"
    s.noise_filter_enabled = form.get("noise_filter_enabled") == "on"
    s.propose_slots_enabled = form.get("propose_slots_enabled") == "on"
    s.commitments_enabled = form.get("commitments_enabled") == "on"
    s.transcripts_enabled = form.get("transcripts_enabled") == "on"
    mm = (form.get("meeting_minutes") or "").strip()
    s.meeting_minutes = int(mm) if mm.isdigit() and 5 <= int(mm) <= 480 else None


def _followup_visible(fu, user) -> bool:
    """A follow-up is visible to the owner of its (personal) mailbox or a member of its
    shared mailbox — same scoping as messages."""
    if fu.organization_id != user.organization_id:
        return False
    acct = fu.account
    if acct is None:
        return fu.user_id == user.id
    if acct.is_shared:
        return user.id in acct.member_user_ids
    return acct.user_id == user.id


@web_bp.route("/app/follow-ups")
def follow_ups():
    from app.constants import DRAFT_CONCEPT
    from app.models import FollowUp

    user = ctx.current_user
    rows = (
        db.session.query(FollowUp)
        .filter(FollowUp.organization_id == user.organization_id, FollowUp.status == DRAFT_CONCEPT)
        .order_by(FollowUp.last_sent_at.asc())
        .all()
    )
    items = [f for f in rows if _followup_visible(f, user)]
    return _render("follow_ups", active="follow_ups", follow_ups=items)


def _appointment_visible(appt, user) -> bool:
    """Zelfde scoping als mail: de eigenaar van het (persoonlijke) postvak, of een lid van
    het gedeelde postvak."""
    if appt.organization_id != user.organization_id:
        return False
    acct = appt.account
    if acct is None:
        return appt.user_id == user.id
    if acct.is_shared:
        return user.id in acct.member_user_ids
    return acct.user_id == user.id


def _resolve_appointment(appt_id: int):
    """Een klaargezette afspraak die de huidige gebruiker mag zien, of 404."""
    from app.models import StagedAppointment

    appt = db.session.get(StagedAppointment, appt_id)
    if not appt or not _appointment_visible(appt, ctx.current_user):
        abort(404)
    return appt


@web_bp.route("/app/appointments")
def appointments():
    """Klaargezette afspraken die op bevestiging wachten (fase 6.4b)."""
    from app.models import StagedAppointment

    user = ctx.current_user
    rows = (
        db.session.query(StagedAppointment)
        .filter(StagedAppointment.organization_id == user.organization_id,
                StagedAppointment.status == "pending")
        .order_by(StagedAppointment.start_at.asc())
        .all()
    )
    items = [a for a in rows if _appointment_visible(a, user)]
    return _render("appointments", active="appointments", appointments=items)


@web_bp.route("/app/appointments/<int:appt_id>/confirm", methods=["POST"])
def appointment_confirm(appt_id: int):
    """Zet de klaargezette afspraak echt in de agenda. Enige plek waar Otto boekt — en
    alleen op deze expliciete klik van de gebruiker."""
    from app.tasks.appointments import book

    appt = _resolve_appointment(appt_id)
    if appt.status != "pending":
        return redirect(url_for("web.appointments"))
    if book(appt):
        flash(_("Afspraak in je agenda gezet."), "toast")
    else:
        flash(_("Kon de afspraak niet in de agenda zetten — probeer het opnieuw."), "error")
    return redirect(url_for("web.appointments"))


@web_bp.route("/app/appointments/<int:appt_id>/dismiss", methods=["POST"])
def appointment_dismiss(appt_id: int):
    appt = _resolve_appointment(appt_id)
    if appt.status == "pending":
        appt.status = "dismissed"
        db.session.commit()
        flash(_("Afspraak weggelegd."), "toast")
    return redirect(url_for("web.appointments"))


@web_bp.route("/app/categories")
def categories():
    return _render("categories", **ctx.categories_context())


@web_bp.route("/app/rules")
def rules():
    """Inbox-regels: wat Otto in Outlook moet laten gebeuren met binnenkomende mail."""
    return _render("rules", **ctx.rules_context(request.args.get("account", type=int)))


@web_bp.route("/app/integrations")
def integrations():
    return _render("integrations", **ctx.integrations_context())


@web_bp.route("/app/integrations/crm/<provider>", methods=["POST"])
def integrations_crm(provider: str):
    """Koppel (of wijzig) een eigen CRM van de organisatie voor het loggen van klantmail
    — Odoo, HubSpot, Pipedrive of een generieke webhook (zie app/crm/)."""
    from app.crm import PROVIDERS
    from app.models import CrmConnection

    meta = PROVIDERS.get(provider)
    if meta is None:
        abort(404)
    user = ctx.current_user
    if not user.has_perm("manageProviders"):
        abort(403)
    conn = (
        db.session.query(CrmConnection)
        .filter_by(organization_id=user.organization_id, provider=provider)
        .first()
    )
    if conn is None:
        conn = CrmConnection(organization_id=user.organization_id, provider=provider)
        db.session.add(conn)
    veldnamen = {f["name"] for f in meta["fields"]}
    for veld in ("base_url", "db_name", "username"):
        if veld in veldnamen:
            setattr(conn, veld, (request.form.get(veld) or "").strip() or None)
    # Een leeg sleutelveld laat de bestaande sleutel staan — zo hoeft de gebruiker hem niet
    # elke keer opnieuw te typen om alleen een schakelaar te wijzigen.
    nieuwe_sleutel = (request.form.get("api_key") or "").strip()
    if nieuwe_sleutel:
        conn.api_key = nieuwe_sleutel
    conn.enabled = bool(request.form.get("enabled"))
    conn.log_incoming = bool(request.form.get("log_incoming"))
    db.session.commit()
    flash(_("%(name)s-koppeling opgeslagen.", name=meta["name"]), "toast")
    return redirect(url_for("web.integrations"))


@web_bp.route("/app/profile")
def profile():
    """Profiel is samengevoegd met Instellingen — de antwoord-instructies en
    handtekening staan daar nu. Oude links/bookmarks blijven werken."""
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings")
def settings():
    return _render("settings", **ctx.settings_context())


@web_bp.route("/app/settings/profiel", methods=["POST"])
def save_profile():
    """Profielvelden (functie/afdeling/locatie/telefoon/mobiel) — voeden de
    handtekening-placeholders. Wat hier wordt ingevuld wint van de automatische
    aanvulling uit Microsoft 365 (die vult alleen lege velden). Daarnaast: de eigen
    waarde van elke zichtbare handtekening-variabele (var-<id>), en optioneel een
    nieuwe PERSOONLIJKE variabele — een gebruiker vult altijd alleen voor zichzelf."""
    from app.models import SignatureVariable, SignatureVariableValue
    from app.signature_central import CENTRAL_FIELDS, is_central
    from app.signatures import slugify_key, variables_for

    user = ctx.current_user
    central = is_central(user.organization)
    # In centrale modus verzamelen we de wijzigingen aan de gedeelde velden (die de
    # centrale handtekening voeden) als VOORSTEL i.p.v. ze meteen op te slaan.
    proposal: dict = {}

    for field, maxlen in CENTRAL_FIELDS.items():
        value = (request.form.get(field) or "").strip()[:maxlen] or None
        if central:
            if value != (getattr(user, field, None) or None):
                proposal[field] = value or ""
        else:
            setattr(user, field, value)

    # Eigen waarde van zichtbare variabelen. Org-brede variabelen voeden de centrale
    # handtekening → in centrale modus als voorstel; persoonlijke blijven altijd direct.
    for var, old in variables_for(user):
        raw = request.form.get(f"var-{var.id}")
        if raw is None:
            continue
        value = raw.strip()[:512]
        if central and not var.is_personal:
            if (value or None) != (old or None):
                proposal[f"var-{var.id}"] = value
            continue
        row = db.session.query(SignatureVariableValue).filter_by(
            variable_id=var.id, user_id=user.id).first()
        if row is None:
            if not value:
                continue
            row = SignatureVariableValue(organization_id=user.organization_id,
                                         variable_id=var.id, user_id=user.id)
            db.session.add(row)
        row.value = value or None

    # Nieuwe persoonlijke variabele (label + waarde in één beweging).
    new_label = (request.form.get("new_var_label") or "").strip()[:128]
    if new_label:
        key = slugify_key(new_label)
        exists = key and db.session.query(SignatureVariable).filter_by(
            organization_id=user.organization_id, key=key).first()
        if not key or exists:
            flash(_("Die variabelenaam kan niet (ongeldig of bestaat al)."), "error")
        else:
            var = SignatureVariable(organization_id=user.organization_id,
                                    user_id=user.id, key=key, label=new_label)
            db.session.add(var)
            db.session.flush()
            new_value = (request.form.get("new_var_value") or "").strip()[:512]
            if new_value:
                db.session.add(SignatureVariableValue(
                    organization_id=user.organization_id, variable_id=var.id,
                    user_id=user.id, value=new_value))

    # Centrale modus met wijzigingen aan gedeelde velden → dien een voorstel in dat IT
    # goedkeurt (of vervang het lopende voorstel). Persoonlijke wijzigingen hierboven zijn
    # al direct opgeslagen.
    if central and proposal:
        from app.models import SignatureChangeRequest

        req = (db.session.query(SignatureChangeRequest)
               .filter_by(user_id=user.id, status="pending").first())
        if req is None:
            req = SignatureChangeRequest(organization_id=user.organization_id, user_id=user.id,
                                         payload=proposal, status="pending")
            db.session.add(req)
        else:
            req.payload = proposal
        db.session.commit()
        flash(_("Je wijziging is ingediend en wacht op goedkeuring door IT."), "toast")
        return redirect(url_for("web.settings"))

    db.session.commit()
    flash(_("Profiel opgeslagen."), "toast")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings/variabele/<int:var_id>/delete", methods=["POST"])
def delete_profile_var(var_id: int):
    """Een PERSOONLIJKE variabele verwijderen — alleen je eigen; org-brede zijn van de admin."""
    from app.models import SignatureVariable

    user = ctx.current_user
    row = db.session.get(SignatureVariable, var_id)
    if not row or row.organization_id != user.organization_id or row.user_id != user.id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Variabele verwijderd."), "toast")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings/voorkeuren", methods=["POST"])
def save_reply_prefs():
    from app.constants import TONES
    from app.models import StyleProfile

    user = ctx.current_user
    style = user.style_profile
    if style is None:
        style = StyleProfile(user_id=user.id)
        db.session.add(style)
    tone = (request.form.get("tone") or "").strip()
    if tone in TONES:
        style.tone = tone
    style.default_reply = (request.form.get("default_reply") or "").strip() or None
    sig = request.form.get("signature")
    if sig is not None:
        style.signature = sig.strip() or None
    # Automation prefs (follow-ups / concept TTL / auto-foldering).
    style.follow_up_enabled = request.form.get("follow_up_enabled") == "on"
    fud = (request.form.get("follow_up_days") or "").strip()
    style.follow_up_days = int(fud) if fud.isdigit() and int(fud) > 0 else 3
    ttl = (request.form.get("draft_ttl_days") or "").strip()
    style.draft_ttl_days = int(ttl) if ttl.isdigit() and int(ttl) > 0 else None
    style.auto_folder_enabled = request.form.get("auto_folder_enabled") == "on"
    style.noise_filter_enabled = request.form.get("noise_filter_enabled") == "on"
    style.propose_slots_enabled = request.form.get("propose_slots_enabled") == "on"
    style.commitments_enabled = request.form.get("commitments_enabled") == "on"
    style.transcripts_enabled = request.form.get("transcripts_enabled") == "on"
    mm = (request.form.get("meeting_minutes") or "").strip()
    style.meeting_minutes = int(mm) if mm.isdigit() and 5 <= int(mm) <= 480 else None
    style.digest_mode = request.form.get("digest_mode") == "on"
    # Onzin hier laten staan zou een lege inbox opleveren; parse_hours normaliseert
    # het naar iets bruikbaars en valt terug op de standaardmomenten.
    hours = (request.form.get("digest_hours") or "").strip()
    style.digest_hours = ",".join(str(h) for h in inbox_digest.parse_hours(hours)) if hours else None
    db.session.commit()
    flash(_("Antwoordvoorkeuren opgeslagen."), "toast")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings/stijl", methods=["POST"])
def save_style_profile():
    """Edit the writing style Otto learned from your sent mail.

    Every field saved here is pinned, so the next learning pass refines what it may and
    leaves your wording alone — otherwise editing would silently revert.
    """
    from app.models import StyleProfile
    from app.tasks.learn import pin_style_fields, unpin_style_fields

    user = ctx.current_user
    style = user.style_profile
    if style is None:
        style = StyleProfile(user_id=user.id)
        db.session.add(style)
        db.session.flush()

    pin, unpin = [], []
    for field in ("greeting", "signoff"):
        value = (request.form.get(field) or "").strip()
        if value:
            setattr(style, field, value[:64])
            pin.append(field)
        else:
            unpin.append(field)  # emptied → let Otto learn it again

    # Example phrasings: one per line, blank means "learn them again".
    raw = dict(style.raw_profile or {})
    lines = [ln.strip() for ln in (request.form.get("examples") or "").splitlines() if ln.strip()]
    if lines:
        raw["examples"] = lines[:5]
        pin.append("examples")
    else:
        raw["examples"] = []
        unpin.append("examples")
    style.raw_profile = raw

    pin_style_fields(style, pin)
    unpin_style_fields(style, unpin)
    db.session.commit()
    flash(_("Schrijfstijl opgeslagen — Otto houdt zich hieraan."), "toast")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings/stijl/leren", methods=["POST"])
def relearn_style():
    """Lees de échte Verzonden-items uit het postvak en laat de AI beschrijven hoe de
    gebruiker antwoordt (deep=True) — de knop doet zo wat ze belooft."""
    from app.tasks.learn import learn_user_style

    user = ctx.current_user
    if learn_user_style(user.id, deep=True) is None:
        flash(_("Nog te weinig verzonden mail om je stijl uit te leren — verbind een postvak "
                "of verstuur eerst een paar antwoorden."), "info")
    else:
        flash(_("Je schrijfstijl is opnieuw geleerd uit je verzonden mail."), "toast")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/settings/synchronisatie", methods=["POST"])
def save_sync_prefs():
    """Set how often the background scheduler polls this user's mailbox(es)."""
    from app.constants import SYNC_INTERVAL_VALUES

    seconds = request.form.get("sync_interval", type=int)
    if seconds not in SYNC_INTERVAL_VALUES:
        flash(_("Ongeldig synchronisatie-interval."), "error")
        return redirect(url_for("web.settings"))
    accounts = [a for a in ctx.current_user.mail_accounts if a.status == "connected"]
    for account in accounts:
        account.sync_interval_seconds = seconds
    db.session.commit()
    if accounts:
        flash(_("Synchronisatie-interval opgeslagen."), "toast")
    else:
        flash(_("Koppel eerst een postvak — dan houdt Otto het automatisch bij."), "info")
    return redirect(url_for("web.settings"))


@web_bp.route("/app/account/delete", methods=["POST"])
def account_delete():
    """Owner deletes their own environment (soft delete, recoverable by the administrator).

    Confirmed by typing the company name + the owner's password. Soft-deleting the org
    locks out every member on their next request (see web.before_request). Best-effort:
    mail the owner a confirmation and notify the instance administrator."""
    from flask_login import logout_user

    from app.email import notify_platform_owner, render_email, send_email
    from app.models import AuditLog
    from app.tasks.schedule import TRASH_RETENTION_DAYS
    from app.tenancy import soft_delete_org

    user = ctx.current_user
    org = user.organization
    _owner_or_403(user, org)
    back = url_for("web.admin_screen", screen="overzicht")

    typed = (request.form.get("confirm_name") or "").strip()
    password = request.form.get("password") or ""
    if typed != org.name:
        flash(_("Bevestiging klopt niet — typ exact “%(name)s” om de omgeving te verwijderen.", name=org.name), "error")
        return redirect(back)
    # Password check when the owner has one (a self-registered owner always does; an
    # SSO-only owner keeps only the name confirmation — they authenticated via the IdP).
    if user.password_hash and not user.check_password(password):
        flash(_("Wachtwoord klopt niet."), "error")
        return redirect(back)

    company, owner_name, owner_email = org.name, user.name, user.email
    db.session.add(AuditLog(
        organization_id=org.id, actor=(user.name or user.email),
        action="verwijderde de omgeving (soft delete)", kind="security",
    ))
    from app.events import record_event

    record_event(org, "org.deleted", detail="door de eigenaar zelf", actor=(user.name or user.email))
    soft_delete_org(org)  # commits; also sets deleted_at → members locked out next request
    # Confirmation to the owner (recovery window).
    try:
        subject, html, text = render_email("env_deleted", name=owner_name, company=company,
                                           restore_days=str(TRASH_RETENTION_DAYS))
        send_email(owner_email, subject, html, text, template_key="env_deleted")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("env_deleted mail skipped: %s", exc)
    notify_platform_owner(
        f"Omgeving verwijderd: {company}",
        f"<p><strong>{company}</strong> is door de eigenaar ({owner_name}, {owner_email}) "
        f"verwijderd (soft delete). {TRASH_RETENTION_DAYS} dagen herstelbaar via /owner.</p>",
    )
    logout_user()
    flash(_("Je omgeving is verwijderd. De gegevens blijven %(days)s dagen bewaard — vraag de "
            "beheerder om ze terug te zetten.", days=TRASH_RETENTION_DAYS), "info")
    return redirect(url_for("auth.login"))


# ── Domeinverificatie (DNS-TXT) ──────────────────────────────────────────────
def _claim_or_404(claim_id: int, org):
    """A claim, scoped to the caller's own organisation."""
    from app.models import DomainClaim

    claim = db.session.get(DomainClaim, claim_id)
    if not claim or claim.organization_id != org.id:
        abort(404)
    return claim


@web_bp.route("/app/domains/claim", methods=["POST"])
def domain_claim():
    """Start a claim on a domain — Otto hands back the TXT record to publish."""
    from app.domains import claim_domain

    user = ctx.current_user
    org = user.organization
    _owner_or_403(user, org)

    claim, error = claim_domain(org, request.form.get("domain") or "")
    if error:
        flash(error, "error")
    elif claim and not claim.is_verified:
        flash(_("Domein toegevoegd. Publiceer het TXT-record en klik daarna op ‘Controleer nu’."), "toast")
    return redirect(url_for("web.connector_settings"))


@web_bp.route("/app/domains/<int:claim_id>/verify", methods=["POST"])
def domain_verify(claim_id: int):
    """Check the TXT record right now (the scheduler also re-checks daily)."""
    from app.domains import verify_claim

    user = ctx.current_user
    org = user.organization
    _owner_or_403(user, org)

    claim = _claim_or_404(claim_id, org)
    if verify_claim(claim):
        flash(_("%(domain)s is geverifieerd.", domain=claim.domain), "toast")
    else:
        flash(claim.last_error or _("Nog niet geverifieerd."), "error")
    return redirect(url_for("web.connector_settings"))


@web_bp.route("/app/domains/<int:claim_id>/remove", methods=["POST"])
def domain_remove(claim_id: int):
    """Drop a claim — frees the domain for another organisation to verify."""
    user = ctx.current_user
    org = user.organization
    _owner_or_403(user, org)

    claim = _claim_or_404(claim_id, org)
    domain = claim.domain
    db.session.delete(claim)
    db.session.commit()
    flash(_("%(domain)s verwijderd.", domain=domain), "toast")
    return redirect(url_for("web.connector_settings"))


@web_bp.route("/app/connector", methods=["GET", "POST"])
def connector_settings():
    """Owner self-service: bring-your-own Microsoft/Google OAuth app per company."""
    from app.connectors import org_uses_own_google, org_uses_own_microsoft

    user = ctx.current_user
    org = user.organization
    is_owner = (user.role and user.role.is_owner) or (org and org.owner_user_id == user.id)
    if not is_owner:
        abort(403)

    # Reached from the onboarding flow (owner not onboarded yet, or ?from_onboarding=1):
    # save returns to onboarding and the "Terug" link points back there, so the setup
    # steps stay one continuous flow.
    from_onboarding = bool(request.values.get("from_onboarding")) or not user.onboarded

    if request.method == "POST":
        from app.security.crypto import encrypt

        org.graph_app_id = (request.form.get("graph_app_id") or "").strip() or None
        org.graph_tenant_id = (request.form.get("graph_tenant_id") or "").strip() or None
        if (sec := (request.form.get("graph_client_secret") or "").strip()):
            org.graph_client_secret_enc = encrypt(sec)
        org.google_client_id = (request.form.get("google_client_id") or "").strip() or None
        if (gsec := (request.form.get("google_client_secret") or "").strip()):
            org.google_client_secret_enc = encrypt(gsec)

        if org.sso is not None:  # login-SSO (OIDC)
            sso = org.sso
            sso.oidc_issuer = (request.form.get("oidc_issuer") or "").strip() or None
            sso.oidc_client_id = (request.form.get("oidc_client_id") or "").strip() or None
            if (osec := (request.form.get("oidc_client_secret") or "").strip()):
                sso.oidc_client_secret_enc = encrypt(osec)
            raw = (request.form.get("allowed_domains") or "").replace(",", " ").split()
            sso.allowed_domains = [d.strip().lower() for d in raw if d.strip()]
            sso.enabled = bool(request.form.get("sso_enabled"))

            # SAML 2.0 — geen versleuteld veld: het certificaat van de IdP is publiek.
            sso.saml_sso_url = (request.form.get("saml_sso_url") or "").strip() or None
            sso.saml_entity_id = (request.form.get("saml_entity_id") or "").strip() or None
            sso.saml_x509_cert = (request.form.get("saml_x509_cert") or "").strip() or None
            sso.method = "SAML 2.0" if request.form.get("sso_prefer_saml") else "OIDC"

        db.session.commit()
        flash(_("Koppelinstellingen opgeslagen."), "toast")
        if from_onboarding:
            flash(_("Je eigen app staat klaar — klik nu op ‘Verbinden’ om te koppelen."), "info")
            return redirect(url_for("auth.onboarding"))
        return redirect(url_for("web.connector_settings"))

    from app.domains import claims_for

    base = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    return _render(
        "connector", org=org, domain_claims=claims_for(org),
        ms_redirect=base + url_for("auth.oauth_microsoft_callback"),
        saml_metadata_url=base + url_for("auth.saml_metadata"),
        saml_acs_url=base + url_for("auth.saml_acs"),
        google_redirect=base + url_for("auth.oauth_google_callback"),
        sso_redirect=base + url_for("auth.sso_callback"),
        uses_own_ms=org_uses_own_microsoft(org), uses_own_google=org_uses_own_google(org),
        from_onboarding=from_onboarding,
    )

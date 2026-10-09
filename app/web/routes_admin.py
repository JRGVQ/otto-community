"""Beheer (admin workspace) screens — each gated by its permission."""
from __future__ import annotations

import re

from flask import abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from app.web import context as ctx
from app.web import web_bp

_BUILDERS = {
    "overzicht": ctx.overview_context,
    "users": ctx.users_context,
    "roles": ctx.roles_context,
    "sso": ctx.sso_context,
    "providers": ctx.providers_context,
    "orgcats": ctx.orgcats_context,
    "ai": ctx.ai_context,
    "audit": ctx.audit_context,
    "signatures": ctx.signatures_context,
    "autoreplies": ctx.autoreplies_context,
    "knowledge": ctx.knowledge_context,
}


@web_bp.route("/admin")
def admin_home():
    return redirect(url_for("web.admin_screen", screen="overzicht"))


@web_bp.route("/admin/<screen>")
def admin_screen(screen: str):
    # Viewing admin screens is open (as in the prototype); mutations are gated
    # per-permission at the API layer (see app/api/admin.py + @require_perm).
    if screen not in _BUILDERS:
        abort(404)
    data = ctx.base_context("admin", screen)
    data.update(_BUILDERS[screen]())
    return render_template(f"app/admin/{screen}.html", **data)


@web_bp.route("/admin/ai/byo", methods=["POST"])
def admin_ai_byo():
    """Save (or clear) the org's own LLM credentials. The key is stored encrypted; a
    blank key field keeps the stored one (so it's never echoed)."""
    from app.extensions import db
    from app.security.crypto import encrypt

    if not current_user.has_perm("manageAI"):
        abort(403)
    # When the form comes from the onboarding flow, return there instead of the admin
    # screen so the owner stays inside the setup steps.
    back = (url_for("auth.onboarding") if request.form.get("next") == "onboarding"
            else url_for("web.admin_screen", screen="ai"))
    ai = ctx._org().ai
    provider = (request.form.get("provider") or "").strip().lower()
    if provider not in {"anthropic", "openai", "azure", "compatible"}:
        ai.byo_provider = ai.byo_api_key_enc = ai.byo_model = ai.byo_base_url = None
        db.session.commit()
        flash(_("Eigen AI-sleutel verwijderd — Otto valt terug op het standaardmodel."), "toast")
        return redirect(back)
    ai.byo_provider = provider
    key = (request.form.get("api_key") or "").strip()
    if key:  # only overwrite when a new key is entered (blank keeps the stored one)
        ai.byo_api_key_enc = encrypt(key)
    # The model comes from a dropdown; picking "Anders…" moves the value to model_custom.
    from app.ai.models import resolve_submitted

    ai.byo_model = resolve_submitted(request.form.get("model"), request.form.get("model_custom"))
    ai.byo_base_url = (request.form.get("base_url") or "").strip() or None
    db.session.commit()
    flash(_("Eigen AI-sleutel opgeslagen."), "toast")
    return redirect(back)


def _parse_dt(raw):
    """Een datetime-local-veld ('YYYY-MM-DDTHH:MM') naar een naïeve datetime, of None."""
    from datetime import datetime

    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _signature_exclusions_from_form():
    """Uitzonderingen uit het formulier: {"users": [id,…], "groups": [guid,…]} of None."""
    users = [u.strip() for u in request.form.getlist("exclude_user_ids") if (u or "").strip().isdigit()]
    groups = []
    for entry in request.form.getlist("exclude_group_ids"):
        gid = (entry or "").partition("|")[0].strip()
        if gid:
            groups.append(gid)
    data = {}
    if users:
        data["users"] = users
    if groups:
        data["groups"] = groups
    return data or None


def _signature_assignments_from_form(org, row):
    """Vervang de toewijzingen van één handtekening door wat het formulier aanlevert.

    user_ids = multi-select van collega's; groups = "guid|naam"-regels uit de kiezer.
    Een gebruiker/groep kan maar aan één handtekening hangen: een nieuwe toewijzing
    trekt hem weg bij de vorige handtekening (dat is wat de beheerder bedoelt)."""
    from app.extensions import db
    from app.models import SignatureAssignment

    wanted: list[tuple[str, str, str | None]] = []
    for uid in request.form.getlist("user_ids"):
        uid = (uid or "").strip()
        if uid.isdigit():
            wanted.append(("user", uid, None))
    for entry in request.form.getlist("group_ids"):
        gid, _, label = (entry or "").partition("|")
        gid = gid.strip()
        if gid:
            wanted.append(("group", gid, label.strip()[:255] or None))

    for old in list(row.assignments):
        db.session.delete(old)
    db.session.flush()
    for kind, value, label in wanted:
        existing = (
            db.session.query(SignatureAssignment)
            .filter_by(organization_id=org.id, kind=kind, value=value)
            .first()
        )
        if existing is not None:
            db.session.delete(existing)  # verhuist van een andere handtekening hierheen
            db.session.flush()
        db.session.add(SignatureAssignment(
            organization_id=org.id, signature_id=row.id, kind=kind, value=value, label=label,
        ))


@web_bp.route("/admin/signatures/save", methods=["POST"])
def admin_signature_save():
    """Create or update one org signature (blank domain + geen toewijzing = de standaard)."""
    from app.extensions import db
    from app.models import Signature
    from app.signatures import normalize

    from app.signatures import domain_conflict

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))
    raw_domain = (request.form.get("domain") or "").strip()
    domain = normalize(raw_domain) if raw_domain else None
    if raw_domain and domain is None:
        flash(_("“%(d)s” is geen geldige domeinnaam.", d=raw_domain), "error")
        return back
    body = (request.form.get("body") or "").strip()
    html = (request.form.get("html") or "").strip() or None
    if not body and not html:
        flash(_("Een handtekening kan niet leeg zijn."), "error")
        return back
    if not body and html:
        # De platte tekst voedt de AI-prompt-fallback en tekst-providers; leid hem af.
        from app.htmltext import html_to_text

        body = html_to_text(html).strip() or "—"

    # Per-handtekening-config: aan/uit, tijdvenster, ontvanger-scope, uitzonderingen.
    # Een niet-aangevinkt checkbox stuurt niets mee; met een verborgen 'enabled_present'-veld
    # weet de route dat het formulier de schakelaar bevat (anders = eenvoudige POST → aan).
    if request.form.get("enabled_present"):
        enabled = request.form.get("enabled") is not None
    else:
        enabled = True
    starts_at = _parse_dt(request.form.get("starts_at"))
    ends_at = _parse_dt(request.form.get("ends_at"))
    if starts_at and ends_at and ends_at <= starts_at:
        flash(_("De einddatum moet ná de startdatum liggen."), "error")
        return back
    recipient_scope = request.form.get("recipient_scope") or "all"
    if recipient_scope not in ("all", "internal", "external"):
        recipient_scope = "all"

    sig_id = request.form.get("sig_id", type=int)
    row = None
    if sig_id:
        row = db.session.get(Signature, sig_id)
        if not row or row.organization_id != org.id:
            abort(404)

    # Foutafhandeling: op één domein mag hoogstens één permanente handtekening staan;
    # extra mag alleen mét een (niet-overlappend) tijdvenster (daarna neemt de standaard weer over).
    clash = domain_conflict(org, domain, starts_at, ends_at, enabled=enabled,
                            exclude_id=row.id if row else None)
    if clash is not None:
        where = _("het domein @%(d)s", d=domain) if domain else _("de standaard")
        flash(_("“%(name)s” is al actief op %(where)s. Geef deze handtekening een tijdvenster dat "
                "niet overlapt, of schakel de andere uit.", name=clash.name or _("Standaard"), where=where), "error")
        return back

    if row is None:
        row = Signature(organization_id=org.id, domain=domain, body=body)
        db.session.add(row)
    row.domain = domain
    row.name = (request.form.get("name") or "").strip()[:64] or (domain or "Standaard")
    row.body = body
    row.html = html
    row.enabled = enabled
    row.starts_at = starts_at
    row.ends_at = ends_at
    row.recipient_scope = recipient_scope
    db.session.flush()  # row.id is nodig voor de toewijzingen hieronder
    _signature_assignments_from_form(org, row)
    row.exclusions = _signature_exclusions_from_form()
    db.session.commit()
    flash(_("Handtekening opgeslagen."), "toast")
    return back


# ── Sjabloon-import: een eigen HTML-ontwerp bewaren in de galerij ──────────────
_SIG_TPL_MAX = 256 * 1024  # 256 KB — ruim voor een handtekening, klein genoeg tegen misbruik


@web_bp.route("/admin/signatures/templates/import", methods=["POST"])
def admin_signature_template_import():
    """Bewaar geplakte of geüploade HTML als herbruikbaar galerij-sjabloon.

    De HTML wordt server-side gesanitized tot e-mailveilige, inline-opgemaakte HTML
    (app/htmlview.sanitize_signature_html); {{placeholders}} blijven behouden."""
    from app.extensions import db
    from app.htmlview import sanitize_signature_html
    from app.models import SignatureTemplate
    from app.signatures import slugify_key

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))

    name = (request.form.get("name") or "").strip()[:128]
    if not name:
        flash(_("Geef het sjabloon een naam."), "error")
        return back
    # Bron: geüpload .html-bestand heeft voorrang, anders het geplakte tekstvak.
    raw = ""
    upload = request.files.get("file")
    if upload and upload.filename:
        if not upload.filename.lower().endswith((".html", ".htm")):
            flash(_("Upload een .html-bestand of plak de HTML."), "error")
            return back
        raw = upload.read(_SIG_TPL_MAX + 1).decode("utf-8", "replace")
    else:
        raw = request.form.get("html") or ""
    if len(raw) > _SIG_TPL_MAX:
        flash(_("Dit sjabloon is te groot (max. 256 KB)."), "error")
        return back

    cleaned = sanitize_signature_html(raw)
    if not cleaned:
        flash(_("Geen bruikbare HTML gevonden om te importeren."), "error")
        return back

    # Uniek, stabiel slug per organisatie (op naam; bij botsing een volgnummer).
    base = slugify_key(name) or "sjabloon"
    slug, n = base, 2
    while db.session.query(SignatureTemplate).filter_by(organization_id=org.id, slug=slug).first():
        slug, n = f"{base}_{n}", n + 1
    db.session.add(SignatureTemplate(
        organization_id=org.id, slug=slug, name=name,
        description=(request.form.get("description") or "").strip()[:255] or None,
        html=cleaned, created_by=current_user.id,
    ))
    db.session.commit()
    flash(_("Sjabloon “%(name)s” geïmporteerd — je vindt het bovenaan de galerij.", name=name), "toast")
    return back


@web_bp.route("/admin/signatures/templates/<int:tpl_id>/delete", methods=["POST"])
def admin_signature_template_delete(tpl_id: int):
    from app.extensions import db
    from app.models import SignatureTemplate

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(SignatureTemplate, tpl_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Sjabloon verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Regel-gebaseerde toewijzing (verzender/ontvanger-voorwaarden) ──────────────
@web_bp.route("/admin/signatures/rules/save", methods=["POST"])
def admin_signature_rule_save():
    """Maak of bewerk één regel: doel-handtekening + eigenschappen (naam/priority/aan) +
    voorwaarden (parallelle velden cond_field/cond_op/cond_value; lege rijen vervallen)."""
    from app.extensions import db
    from app.models import Signature, SignatureRule, SignatureRuleCondition
    from app.signatures import RULE_FIELDS, RULE_OPS

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))

    sig_id = request.form.get("signature_id", type=int)
    sig = db.session.get(Signature, sig_id) if sig_id else None
    if not sig or sig.organization_id != org.id:
        flash(_("Kies een geldige handtekening voor de regel."), "error")
        return back

    rule_id = request.form.get("rule_id", type=int)
    rule = None
    if rule_id:
        rule = db.session.get(SignatureRule, rule_id)
        if not rule or rule.organization_id != org.id:
            abort(404)
    if rule is None:
        rule = SignatureRule(organization_id=org.id)
        db.session.add(rule)
    rule.signature_id = sig.id
    rule.name = (request.form.get("name") or "").strip()[:128] or None
    rule.priority = request.form.get("priority", type=int) or 100
    rule.enabled = request.form.get("enabled") == "on"
    db.session.flush()

    for old in list(rule.conditions):
        db.session.delete(old)
    db.session.flush()
    fields = request.form.getlist("cond_field")
    ops = request.form.getlist("cond_op")
    values = request.form.getlist("cond_value")
    kept = 0
    for i, field in enumerate(fields):
        field = (field or "").strip()
        if field not in RULE_FIELDS:
            continue
        op = (ops[i] if i < len(ops) else "equals").strip()
        if op not in RULE_OPS:
            op = "equals"
        value = (values[i] if i < len(values) else "").strip()[:255]
        if not value:
            continue
        db.session.add(SignatureRuleCondition(
            organization_id=org.id, rule_id=rule.id, field=field, op=op, value=value))
        kept += 1
    if kept == 0:
        db.session.rollback()
        flash(_("Voeg minstens één voorwaarde toe — een regel zonder voorwaarden doet niets."), "error")
        return back
    db.session.commit()
    flash(_("Regel opgeslagen."), "toast")
    return back


@web_bp.route("/admin/signatures/rules/<int:rule_id>/delete", methods=["POST"])
def admin_signature_rule_delete(rule_id: int):
    from app.extensions import db
    from app.models import SignatureRule

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(SignatureRule, rule_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Regel verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Ingebedde afbeeldingen (logo's/banners → CID-bijlage) ──────────────────────
_SIG_ASSET_MAX = 2 * 1024 * 1024  # 2 MB — ruim voor een logo/banner
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}


@web_bp.route("/admin/signatures/assets/upload", methods=["POST"])
def admin_signature_asset_upload():
    """Upload een logo/banner. In de aflevering gaat het als verborgen inline-bijlage
    (Content-ID) mee, zodat mailclients het niet als externe afbeelding blokkeren.
    Verwijs er in de handtekening naar met de getoonde /media/sig-asset/<id>-URL."""
    from app.extensions import db
    from app.models import SignatureAsset

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))
    upload = request.files.get("file")
    if not (upload and upload.filename):
        flash(_("Kies een afbeelding om te uploaden."), "error")
        return back
    content_type = (upload.mimetype or "").lower()
    if content_type not in _IMAGE_TYPES:
        flash(_("Alleen PNG, JPEG, GIF, WEBP of SVG."), "error")
        return back
    data = upload.read(_SIG_ASSET_MAX + 1)
    if len(data) > _SIG_ASSET_MAX:
        flash(_("Deze afbeelding is te groot (max. 2 MB)."), "error")
        return back
    kind = (request.form.get("kind") or "image").strip().lower()
    if kind not in {"logo", "banner", "image"}:
        kind = "image"
    db.session.add(SignatureAsset(
        organization_id=org.id, kind=kind, content_type=content_type,
        filename=(upload.filename or "")[:255], data=data, created_by=current_user.id,
    ))
    db.session.commit()
    flash(_("Afbeelding geüpload — voeg ze in de editor in of gebruik de getoonde URL."), "toast")
    return back


@web_bp.route("/admin/signatures/assets/<int:asset_id>/delete", methods=["POST"])
def admin_signature_asset_delete(asset_id: int):
    from app.extensions import db
    from app.models import SignatureAsset

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(SignatureAsset, asset_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Afbeelding verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Afwezigheidsassistent (out-of-office) ─────────────────────────────────────
@web_bp.route("/admin/autoreplies/save", methods=["POST"])
def admin_autoreply_save():
    """Bewaar de OOO-instelling voor een eigen gekoppeld Outlook-postvak; de sync duwt ze
    naar Exchange (mailboxSettings). Delegated tokens zijn per gebruiker, dus alleen eigen
    postvakken — een echte org-brede OOO zou app-only rechten vragen (buiten scope)."""
    from app.extensions import db
    from app.models import AutoReplySetting, MailAccount

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    account_id = request.form.get("account_id", type=int)
    acct = db.session.get(MailAccount, account_id)
    if not acct or acct.user_id != current_user.id or acct.provider != "outlook":
        abort(404)
    row = db.session.query(AutoReplySetting).filter_by(mail_account_id=acct.id).first()
    if row is None:
        row = AutoReplySetting(organization_id=current_user.organization_id, mail_account_id=acct.id)
        db.session.add(row)
    status = request.form.get("status")
    row.status = status if status in {"disabled", "scheduled", "always"} else "disabled"
    audience = request.form.get("external_audience")
    row.external_audience = audience if audience in {"none", "contactsOnly", "all"} else "all"
    row.internal_reply_html = (request.form.get("internal_reply_html") or "").strip() or None
    row.external_reply_html = (request.form.get("external_reply_html") or "").strip() or None
    row.scheduled_start = _dt_or_none(request.form.get("scheduled_start"))
    row.scheduled_end = _dt_or_none(request.form.get("scheduled_end"))
    row.push_state = "pending"  # markeer voor de scheduler
    row.last_error = None
    db.session.commit()
    flash(_("Afwezigheidsassistent opgeslagen — wordt zo doorgezet naar Outlook."), "toast")
    return redirect(url_for("web.admin_screen", screen="autoreplies"))


# ── Banners (boven/onder de handtekening; eventueel gepland) ───────────────────
def _can_campaigns() -> bool:
    """Banners + brand kits mogen door de handtekening-admin (manageOrgCats) én door een
    gedelegeerde campagne-rol (manageSignatureCampaigns) — bv. marketing zonder volledige
    handtekening-rechten."""
    return current_user.has_perm("manageOrgCats") or current_user.has_perm("manageSignatureCampaigns")


def _dt_or_none(raw: str | None):
    """<input type=datetime-local> → naïeve datetime (UTC-conventie), of None."""
    from datetime import datetime

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


@web_bp.route("/admin/signatures/banners/save", methods=["POST"])
def admin_signature_banner_save():
    from app.extensions import db
    from app.models import SignatureBanner

    if not _can_campaigns():
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))
    image_url = (request.form.get("image_url") or "").strip()[:512]
    if not image_url:
        flash(_("Geef een afbeeldings-URL voor de banner."), "error")
        return back
    banner_id = request.form.get("banner_id", type=int)
    row = None
    if banner_id:
        row = db.session.get(SignatureBanner, banner_id)
        if not row or row.organization_id != org.id:
            abort(404)
    if row is None:
        row = SignatureBanner(organization_id=org.id, image_url=image_url)
        db.session.add(row)
    row.name = (request.form.get("name") or "").strip()[:128] or None
    row.image_url = image_url
    row.link_url = (request.form.get("link_url") or "").strip()[:512] or None
    row.starts_at = _dt_or_none(request.form.get("starts_at"))
    row.ends_at = _dt_or_none(request.form.get("ends_at"))
    row.placement = "above" if request.form.get("placement") == "above" else "below"
    row.weight = request.form.get("weight", type=int) or 0
    row.enabled = request.form.get("enabled") == "on"
    # Doelbereik: org / domein / afdeling / M365-groep.
    scope_kind = request.form.get("scope_kind")
    row.scope_kind = scope_kind if scope_kind in {"org", "domain", "department", "group"} else "org"
    row.scope_value = ((request.form.get("scope_value") or "").strip()[:255] or None
                       if row.scope_kind != "org" else None)
    row.track = request.form.get("track") == "on"
    db.session.commit()
    flash(_("Banner opgeslagen."), "toast")
    return back


@web_bp.route("/admin/signatures/banners/<int:banner_id>/delete", methods=["POST"])
def admin_signature_banner_delete(banner_id: int):
    from app.extensions import db
    from app.models import SignatureBanner

    if not _can_campaigns():
        abort(403)
    row = db.session.get(SignatureBanner, banner_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Banner verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Brand kits (centrale huisstijl per organisatie/domein) ─────────────────────
@web_bp.route("/admin/signatures/brand/save", methods=["POST"])
def admin_signature_brand_save():
    """Maak of bewerk één brand kit (logo + accentkleur), org-breed of per verzenddomein.
    Eén kit per (org, domein): bij een botsing wordt de bestaande bijgewerkt."""
    from app.extensions import db
    from app.models import BrandKit
    from app.signatures import normalize

    if not _can_campaigns():
        abort(403)
    org = current_user.organization
    back = redirect(url_for("web.admin_screen", screen="signatures"))
    raw_domain = (request.form.get("domain") or "").strip()
    domain = normalize(raw_domain) if raw_domain else None
    if raw_domain and domain is None:
        flash(_("“%(d)s” is geen geldige domeinnaam.", d=raw_domain), "error")
        return back
    brand_id = request.form.get("brand_id", type=int)
    row = None
    if brand_id:
        row = db.session.get(BrandKit, brand_id)
        if not row or row.organization_id != org.id:
            abort(404)
    if row is None:
        row = db.session.query(BrandKit).filter_by(organization_id=org.id, domain=domain).first()
    if row is None:
        row = BrandKit(organization_id=org.id, domain=domain)
        db.session.add(row)
    row.domain = domain
    row.name = (request.form.get("name") or "").strip()[:128] or (domain or "Standaard")
    row.logo_url = (request.form.get("logo_url") or "").strip()[:512] or None
    row.accent_color = (request.form.get("accent_color") or "").strip()[:16] or None
    # Social-profielen: alleen geldige http(s)-URL's bewaren, per netwerk.
    from app.signatures import SOCIAL_NETWORKS

    socials = {}
    for key, _label in SOCIAL_NETWORKS:
        val = (request.form.get(f"social_{key}") or "").strip()[:512]
        if val.lower().startswith(("http://", "https://")):
            socials[key] = val
    row.socials = socials or None
    db.session.commit()
    flash(_("Brand kit opgeslagen."), "toast")
    return back


@web_bp.route("/admin/signatures/brand/<int:brand_id>/delete", methods=["POST"])
def admin_signature_brand_delete(brand_id: int):
    from app.extensions import db
    from app.models import BrandKit

    if not _can_campaigns():
        abort(403)
    row = db.session.get(BrandKit, brand_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Brand kit verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Eigen handtekening-variabelen (org-breed; de admin vult voor iedereen in) ──
@web_bp.route("/admin/signatures/vars/add", methods=["POST"])
def admin_signature_var_add():
    from app.extensions import db
    from app.models import SignatureVariable
    from app.signatures import slugify_key

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    label = (request.form.get("label") or "").strip()[:128]
    key = slugify_key(label)
    if not key:
        flash(_("Geef een bruikbare naam (letters, geen vaste placeholder zoals “naam”)."), "error")
        return redirect(url_for("web.admin_screen", screen="signatures"))
    if db.session.query(SignatureVariable).filter_by(organization_id=org.id, key=key).first():
        flash(_("Er bestaat al een variabele met deze naam."), "error")
        return redirect(url_for("web.admin_screen", screen="signatures"))
    # Optioneel: koppel aan een M365-/Entra-attribuut (bv. city, extensionAttribute3).
    # Dan wordt de waarde tijdens de sync uit Graph gehaald i.p.v. handmatig ingevuld.
    graph_attr = _clean_graph_attr(request.form.get("graph_attr"))
    db.session.add(SignatureVariable(organization_id=org.id, user_id=None, key=key,
                                     label=label, graph_attr=graph_attr))
    db.session.commit()
    if graph_attr:
        flash(_("Variabele “{{%(key)s}}” gekoppeld aan M365-veld “%(attr)s” — de waarden "
                "verschijnen bij de volgende profielverversing.", key=key, attr=graph_attr), "toast")
    else:
        flash(_("Variabele “{{%(key)s}}” toegevoegd — vul hieronder de waarden in.", key=key), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# Toegestane vorm van een Graph-attribuut: veld, geneste (onPremises…extensionAttributeN)
# of schema-extension (extension_<appId>_naam). Letters/cijfers/_/. — geen rare tekens.
_GRAPH_ATTR_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)?$")


def _clean_graph_attr(raw: str | None) -> str | None:
    attr = (raw or "").strip()[:128]
    return attr if attr and _GRAPH_ATTR_RE.match(attr) else None


@web_bp.route("/admin/signatures/vars/<int:var_id>/delete", methods=["POST"])
def admin_signature_var_delete(var_id: int):
    from app.extensions import db
    from app.models import SignatureVariable

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(SignatureVariable, var_id)
    # Alleen org-brede variabelen: persoonlijke zijn van de gebruiker zelf.
    if not row or row.organization_id != current_user.organization_id or row.user_id is not None:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Variabele verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


@web_bp.route("/admin/signatures/vars/values", methods=["POST"])
def admin_signature_var_values():
    """De waardenmatrix: de admin vult (of corrigeert) de waarde van elke org-brede
    variabele voor elke collega. Veldnamen: val-<variabele-id>-<user-id>."""
    from app.extensions import db
    from app.models import SignatureVariable, SignatureVariableValue, User

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    org_vars = {v.id: v for v in db.session.query(SignatureVariable)
                .filter_by(organization_id=org.id, user_id=None).all()}
    org_users = {u.id for u in db.session.query(User.id).filter_by(organization_id=org.id).all()}
    for field, raw in request.form.items():
        if not field.startswith("val-"):
            continue
        try:
            _prefix, var_id, user_id = field.split("-", 2)
            var_id, user_id = int(var_id), int(user_id)
        except ValueError:
            continue
        if var_id not in org_vars or user_id not in org_users:
            continue
        value = (raw or "").strip()[:512]
        row = db.session.query(SignatureVariableValue).filter_by(
            variable_id=var_id, user_id=user_id).first()
        if row is None:
            if not value:
                continue
            row = SignatureVariableValue(organization_id=org.id, variable_id=var_id, user_id=user_id)
            db.session.add(row)
        row.value = value or None
    db.session.commit()
    flash(_("Waarden opgeslagen."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


@web_bp.route("/admin/signatures/<int:sig_id>/test", methods=["POST"])
def admin_signature_test(sig_id: int):
    """Stuur de handtekening als testmail naar de beheerder zelf, met de placeholders
    ingevuld met zijn eigen gegevens — precies zoals hij onder een concept komt."""
    from app.email import send_email
    from app.extensions import db
    from app.models import Signature
    from app.signatures import render_placeholders

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(Signature, sig_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)

    intro_txt = _("Dit is een testmail van Otto. Hieronder staat de handtekening “%(name)s” "
                  "zoals hij onder een concept komt.", name=row.name or _("Standaard"))
    if row.html:
        sig_html = render_placeholders(row.html, current_user, html=True)
    else:
        import html as _html

        sig_html = _html.escape(render_placeholders(row.body, current_user)).replace("\n", "<br>")
    html_body = (
        f"<p style=\"color:#57606a;font-size:13px;\">{intro_txt}</p>"
        "<hr style=\"border:none;border-top:1px solid #e3e6ea;margin:16px 0;\">"
        f"{sig_html}"
    )
    text_body = f"{intro_txt}\n\n----------\n{render_placeholders(row.body, current_user)}"
    ok = send_email(current_user.email, _("Test van je handtekening — Otto"), html_body, text_body)
    if ok:
        flash(_("Testmail verstuurd naar %(email)s.", email=current_user.email), "toast")
    else:
        flash(_("Versturen lukte niet — er is geen mailer geconfigureerd voor dit platform."), "error")
    return redirect(url_for("web.admin_screen", screen="signatures"))


@web_bp.route("/admin/signatures/<int:sig_id>/delete", methods=["POST"])
def admin_signature_delete(sig_id: int):
    from app.extensions import db
    from app.models import Signature

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(Signature, sig_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Handtekening verwijderd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Cloud-modus (Exchange transport-regel) ─────────────────────────────────────
def _cloud_setting(org):
    from app.extensions import db
    from app.models import CloudSignatureSetting

    row = db.session.query(CloudSignatureSetting).filter_by(organization_id=org.id).first()
    if row is None:
        row = CloudSignatureSetting(organization_id=org.id)
        db.session.add(row)
    return row


@web_bp.route("/admin/signatures/cloud/save", methods=["POST"])
def admin_signature_cloud_save():
    """Kies de afleveringsmodus (client/cloud/combo) en de aansturende handtekening.
    Bij een wijziging vervalt de attestatie: de admin moet de regel opnieuw bevestigen."""
    from app.extensions import db
    from app.models import Signature

    if not current_user.has_perm("manageProviders"):
        abort(403)
    org = current_user.organization
    row = _cloud_setting(org)
    mode = request.form.get("mode")
    new_mode = mode if mode in {"client", "cloud", "combo"} else "client"
    sig_id = request.form.get("signature_id", type=int)
    sig = db.session.get(Signature, sig_id) if sig_id else None
    new_sig_id = sig.id if (sig and sig.organization_id == org.id) else None
    if row.mode != new_mode or row.signature_id != new_sig_id:
        row.rule_attested_at = None  # regel-definitie wijzigde → opnieuw toepassen
    row.mode = new_mode
    row.signature_id = new_sig_id
    db.session.commit()
    flash(_("Cloud-modus opgeslagen."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


@web_bp.route("/admin/signatures/cloud/attest", methods=["POST"])
def admin_signature_cloud_attest():
    """De admin bevestigt dat de transport-regel in Exchange is toegepast (Graph kan de
    regel niet teruglezen, dus dit is een attestatie met tijdstempel)."""
    from app.extensions import db
    from app.models.base import utcnow

    if not current_user.has_perm("manageProviders"):
        abort(403)
    row = _cloud_setting(current_user.organization)
    row.rule_attested_at = utcnow()
    db.session.commit()
    flash(_("Bedankt — de cloud-regel staat als toegepast gemarkeerd."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Centraal beheer: gebruiker-voorstellen met IT-goedkeuring ──────────────────
@web_bp.route("/admin/signatures/central/save", methods=["POST"])
def admin_signature_central_save():
    """Zet centraal beheer aan/uit. Aan = gebruikers moeten wijzigingen aan hun
    handtekening-velden laten goedkeuren i.p.v. ze direct op te slaan."""
    from app.extensions import db

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = _cloud_setting(current_user.organization)
    row.central_management = request.form.get("central_management") == "on"
    db.session.commit()
    flash(_("Instelling opgeslagen."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


def _pending_request(req_id: int):
    from app.extensions import db
    from app.models import SignatureChangeRequest

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(SignatureChangeRequest, req_id)
    if not row or row.organization_id != current_user.organization_id or row.status != "pending":
        abort(404)
    return row


@web_bp.route("/admin/signatures/requests/<int:req_id>/approve", methods=["POST"])
def admin_signature_request_approve(req_id: int):
    """Keur een voorstel goed: pas de voorgestelde waarden toe op de echte profielvelden."""
    from app.extensions import db
    from app.models.base import utcnow
    from app.signature_central import apply_payload

    row = _pending_request(req_id)
    if row.user is not None:
        apply_payload(row.user, row.payload or {})
    row.status = "approved"
    row.reviewed_by = current_user.id
    row.reviewed_at = utcnow()
    db.session.commit()
    flash(_("Voorstel goedgekeurd en toegepast."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


@web_bp.route("/admin/signatures/requests/<int:req_id>/reject", methods=["POST"])
def admin_signature_request_reject(req_id: int):
    from app.extensions import db
    from app.models.base import utcnow

    row = _pending_request(req_id)
    row.status = "rejected"
    row.reviewed_by = current_user.id
    row.reviewed_at = utcnow()
    row.note = (request.form.get("note") or "").strip()[:255] or None
    db.session.commit()
    flash(_("Voorstel afgewezen."), "toast")
    return redirect(url_for("web.admin_screen", screen="signatures"))


# ── Bedrijfskennis ───────────────────────────────────────────────────────────
def _knowledge_back():
    return redirect(url_for("web.admin_screen", screen="knowledge"))


def _knowledge_account(org, raw: str | None):
    """De gedeelde mailbox waaraan een blok hangt, of None voor organisatiebreed.
    Een onbekende of andermans mailbox valt terug op organisatiebreed in plaats van
    op een 500 — de scoping blijft zo altijd binnen de eigen organisatie."""
    from app.extensions import db
    from app.models import MailAccount

    if not raw:
        return None
    acct = db.session.get(MailAccount, int(raw)) if str(raw).isdigit() else None
    if not acct or acct.organization_id != org.id or not acct.is_shared:
        return None
    return acct


@web_bp.route("/admin/knowledge", methods=["POST"])
def admin_knowledge_save():
    """Eén kennisblok toevoegen of bijwerken (leeg id = nieuw)."""
    from app import knowledge_base
    from app.extensions import db
    from app.models import KnowledgeEntry

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not (title and body):
        flash(_("Geef het blok een titel én inhoud."), "error")
        return _knowledge_back()

    entry_id = request.form.get("entry_id", type=int)
    if entry_id:
        row = db.session.get(KnowledgeEntry, entry_id)
        if not row or row.organization_id != org.id:
            abort(404)
        row.title, row.body = title[:200], body[:knowledge_base.BODY_MAX]
        row.account_id = getattr(_knowledge_account(org, request.form.get("account_id")), "id", None)
        db.session.commit()
        flash(_("Kennisblok bijgewerkt."), "toast")
    else:
        knowledge_base.create(org, title=title, body=body,
                              account=_knowledge_account(org, request.form.get("account_id")))
        flash(_("Kennisblok toegevoegd."), "toast")
    return _knowledge_back()


@web_bp.route("/admin/knowledge/upload", methods=["POST"])
def admin_knowledge_upload():
    """Een document uploaden: Otto haalt er de tekst uit en bewaart die als blok. Het
    bestand zelf wordt niet bijgehouden — alleen de tekst gaat ooit naar de LLM."""
    from app import knowledge_base

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    org = current_user.organization
    f = request.files.get("file")
    if not f or not f.filename:
        flash(_("Geen bestand ontvangen."), "error")
        return _knowledge_back()
    # Nooit meer inlezen dan de grens toelaat: zo kan een groot bestand het geheugen
    # niet opblazen vóór extract_text() erover kan oordelen.
    text, err = knowledge_base.extract_text(f.filename, f.read(knowledge_base.MAX_UPLOAD_BYTES + 1))
    if err:
        flash(err, "error")
        return _knowledge_back()
    if not text.strip():
        flash(_("Er stond geen tekst in dit bestand."), "error")
        return _knowledge_back()
    title = (request.form.get("title") or "").strip() or f.filename.rsplit(".", 1)[0]
    knowledge_base.create(
        org, title=title, body=text, source="file", filename=f.filename,
        account=_knowledge_account(org, request.form.get("account_id")),
    )
    flash(_("Document ingelezen als kennisblok."), "toast")
    return _knowledge_back()


@web_bp.route("/admin/knowledge/<int:entry_id>/delete", methods=["POST"])
def admin_knowledge_delete(entry_id: int):
    from app.extensions import db
    from app.models import KnowledgeEntry

    if not current_user.has_perm("manageOrgCats"):
        abort(403)
    row = db.session.get(KnowledgeEntry, entry_id)
    if not row or row.organization_id != current_user.organization_id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(_("Kennisblok verwijderd."), "toast")
    return _knowledge_back()

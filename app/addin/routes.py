"""Routes voor het Outlook-taakvenster."""
from __future__ import annotations

import uuid

from flask import Response, current_app, jsonify, render_template, request, url_for
from flask_login import current_user

from app.addin import addin_bp
from app.extensions import db


def addin_guid() -> str:
    """De id van de invoegtoepassing — Outlook herkent er updates aan. Uniek per
    installatie: ADDIN_ID uit de configuratie, anders stabiel afgeleid van SECRET_KEY
    (zo botsen twee Otto-installaties nooit binnen één Microsoft 365-tenant)."""
    configured = current_app.config.get("ADDIN_ID")
    if configured:
        return configured
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otto-addin:" + str(current_app.config["SECRET_KEY"])))


def _base_url() -> str:
    """De externe https-basis waarop Outlook de bestanden bereikt. Achter de proxy zorgt
    ProxyFix dat dit de publieke host + https is."""
    return request.url_root.rstrip("/")


@addin_bp.route("/manifest.xml")
def manifest():
    xml = render_template("addin/manifest.xml", guid=addin_guid(), base=_base_url())
    return Response(xml, mimetype="application/xml")


@addin_bp.route("/taskpane")
def taskpane():
    return render_template("addin/taskpane.html", login_url=url_for("auth.login", _external=True))


def _rendered_signatures(user) -> list[dict]:
    """De handtekeningen die deze gebruiker in Outlook kan kiezen, elk met zíjn
    placeholders ingevuld en gesanitized. De aanbevolen (resolved) staat vooraan en
    krijgt `default: true`, zodat de auto-toepassing die kan pakken."""
    from app.htmlview import sanitize_email_html
    from app.models import Signature
    from app.signatures import render_placeholders, resolve_rich

    def _clean(html: str) -> str:
        filled = render_placeholders(html or "", user, html=True)
        return sanitize_email_html(filled) or filled

    out: list[dict] = []
    seen: set[str] = set()

    # 1. De aanbevolen handtekening (zelfde keuze als bij Otto's eigen concepten).
    rec = resolve_rich(user)
    if rec.html:
        html = _clean(rec.html)
        out.append({"id": "recommended", "name": rec.name or _default_name(), "html": html, "default": True})
        seen.add(html)

    # 2. Alle org-handtekeningen met een HTML-versie, zodat je vrij kan kiezen.
    org_id = getattr(user, "organization_id", None)
    if org_id:
        rows = (
            db.session.query(Signature)
            .filter(Signature.organization_id == org_id, Signature.html.isnot(None))
            .order_by(Signature.domain.is_(None).desc(), Signature.id)
            .all()
        )
        for r in rows:
            html = _clean(r.html)
            if html in seen:
                continue
            seen.add(html)
            out.append({"id": str(r.id), "name": r.name or r.domain or _default_name(),
                        "html": html, "default": False})
    return out


def _default_name() -> str:
    from flask_babel import gettext as _

    return _("Handtekening")


@addin_bp.route("/signatures")
def signatures():
    """De kiesbare handtekeningen voor het taakvenster (ingevuld voor de ingelogde
    gebruiker). Checkt zelf op login, net als /addin/context."""
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False, "signatures": []})
    return jsonify({"authenticated": True, "signatures": _rendered_signatures(current_user)})


def _find_message(user, subject: str, sender: str, conversation: str):
    """Het Otto-bericht dat hoort bij de mail die in Outlook openstaat, of None.

    Outlook geeft ons geen Graph-id dat we rechtstreeks kunnen opzoeken; we matchen daarom
    op de conversatie (als die overeenkomt met wat we bewaard hebben) en anders op
    onderwerp + afzender. Altijd gescoped op wat de gebruiker mag zien."""
    from app.models import MailAccount, Message

    q = db.session.query(Message).outerjoin(MailAccount, Message.account_id == MailAccount.id).filter(
        Message.organization_id == user.organization_id
    )
    kandidaten = []
    conv = (conversation or "").strip()
    if conv:
        kandidaten = (
            q.filter(Message.provider_thread_id == conv)
            .order_by(Message.received_at.desc().nullslast(), Message.id.desc())
            .limit(10).all()
        )
    if not kandidaten and (subject or sender):
        qq = q
        if subject:
            qq = qq.filter(Message.subject == subject[:512])
        if sender:
            qq = qq.filter(Message.sender_email == sender[:255])
        kandidaten = qq.order_by(Message.received_at.desc().nullslast(), Message.id.desc()).limit(10).all()
    for m in kandidaten:
        if m.visible_to(user):
            return m
    return None


def _snippets(user, message):
    """De snippets voor dit postvak: organisatiebreed plus, bij een gedeeld postvak, de
    eigen snippets van dat postvak."""
    from app.models import Snippet

    q = db.session.query(Snippet).filter(Snippet.organization_id == user.organization_id)
    acct_id = message.account_id if message is not None else None
    if acct_id is not None:
        q = q.filter(db.or_(Snippet.account_id.is_(None), Snippet.account_id == acct_id))
    else:
        q = q.filter(Snippet.account_id.is_(None))
    return q.order_by(Snippet.position, Snippet.id).limit(12).all()


@addin_bp.route("/context")
def context():
    """De gegevens voor het taakvenster, als JSON. Checkt zelf op een ingelogde gebruiker."""
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})

    subject = request.args.get("subject", "", type=str)
    sender = request.args.get("from", "", type=str)
    conversation = request.args.get("conversation", "", type=str)
    message = _find_message(current_user, subject, sender, conversation)

    from app.constants import DRAFT_CONCEPT
    from app.models import Task

    data: dict = {"authenticated": True, "found": message is not None,
                  "category": None, "draft": None, "tasks": [], "snippets": []}

    if message is not None:
        if message.category is not None:
            data["category"] = {"key": message.category.key, "name": message.category.name}
        draft = message.draft
        if draft is not None and draft.status == DRAFT_CONCEPT and draft.current_text:
            data["draft"] = {"text": draft.current_text, "tone": draft.current_tone}
        taken = (
            db.session.query(Task)
            .filter(Task.user_id == current_user.id, Task.linked_message_id == message.id)
            .order_by(Task.done, Task.id.desc()).limit(10).all()
        )
        data["tasks"] = [
            {"title": t.title, "due": t.due_display or "", "done": bool(t.done)} for t in taken
        ]

    data["snippets"] = [
        {"title": s.title, "body": s.body} for s in _snippets(current_user, message)
    ]
    return jsonify(data)

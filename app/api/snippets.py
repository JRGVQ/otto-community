"""Snippets — herbruikbare tekstblokken voor antwoorden.

Ophalen gebeurt per bericht: get_message dwingt de zichtbaarheid af (inclusief
lidmaatschap van een gedeelde mailbox), dus wie de mail niet mag zien, krijgt ook de
blokken van die mailbox niet.
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from app import snippets as svc
from app.api import api_bp
from app.api._helpers import get_message, ok
from app.extensions import db
from app.models import MailAccount, Snippet


def _json(s: Snippet) -> dict:
    return {"id": s.id, "title": s.title, "body": s.body, "org_wide": s.is_org_wide}


@api_bp.route("/snippets", methods=["GET"])
@login_required
def list_snippets():
    """De blokken die bij dit bericht bruikbaar zijn (organisatiebreed + de mailbox)."""
    message_id = request.args.get("message_id", type=int)
    msg = get_message(message_id) if message_id else None
    return ok(snippets=[_json(s) for s in svc.for_message(current_user, msg)])


@api_bp.route("/snippets/<int:snippet_id>/used", methods=["POST"])
@login_required
def snippet_used(snippet_id: int):
    """Tel een invoeging — zo zie je welke blokken het team echt gebruikt."""
    snippet = db.session.get(Snippet, snippet_id)
    if not snippet or snippet.organization_id != current_user.organization_id:
        abort(404)
    svc.mark_used(snippet)
    return ok()


def _account_or_404(account_id: int | None) -> MailAccount | None:
    """De mailbox waarvoor beheerd wordt, of None voor een organisatiebreed blok."""
    if not account_id:
        return None
    acct = db.session.get(MailAccount, account_id)
    if not acct or acct.organization_id != current_user.organization_id:
        abort(404)
    allowed = current_user.id in acct.member_user_ids if acct.is_shared else acct.user_id == current_user.id
    if not allowed:
        abort(403)
    return acct


@api_bp.route("/snippets", methods=["POST"])
@login_required
def create_snippet():
    account = _account_or_404(request.form.get("account_id", type=int))
    snippet = svc.create(
        current_user.organization,
        title=request.form.get("title", ""),
        body=request.form.get("body", ""),
        account=account,
    )
    if snippet is None:
        return ok(error="Geef een titel en een tekst."), 400
    return ok(snippet=_json(snippet), toast="Blok opgeslagen.", reload=True)


@api_bp.route("/snippets/<int:snippet_id>", methods=["DELETE", "POST"])
@login_required
def delete_snippet(snippet_id: int):
    if request.method == "POST" and request.form.get("_method") != "delete":
        abort(405)
    snippet = db.session.get(Snippet, snippet_id)
    if not svc.manageable(current_user, snippet):
        abort(404)
    db.session.delete(snippet)
    db.session.commit()
    return ok(toast="Blok verwijderd.", reload=True)

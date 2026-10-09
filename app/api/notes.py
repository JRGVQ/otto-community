"""Internal team notes on a message — collaboration on shared mailboxes.

A note is internal context for the team; it is never part of the e-mail and never sent.
Visibility (incl. shared-mailbox membership) is enforced by get_message, so non-members
can neither read nor post notes on a shared mailbox's mail.
"""
from __future__ import annotations

from flask import request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import get_message, ok
from app.extensions import db
from app.models import MessageNote


def _note_json(n: MessageNote) -> dict:
    return {
        "id": n.id,
        "author": n.author.name if n.author else "?",
        "body": n.body,
        "when": n.created_at.strftime("%d/%m %H:%M") if n.created_at else "zojuist",
    }


@api_bp.route("/messages/<int:message_id>/notes", methods=["GET"])
@login_required
def list_notes(message_id: int):
    msg = get_message(message_id)
    return ok(notes=[_note_json(n) for n in sorted(msg.notes, key=lambda n: n.id)])


@api_bp.route("/messages/<int:message_id>/notes", methods=["POST"])
@login_required
def add_note(message_id: int):
    msg = get_message(message_id)
    data = request.get_json(silent=True) or request.form
    body = (data.get("body") or "").strip()
    if not body:
        return ok(error="Lege notitie."), 400
    note = MessageNote(
        message_id=msg.id, organization_id=msg.organization_id,
        author_user_id=current_user.id, body=body[:4000],
    )
    db.session.add(note)
    db.session.commit()
    return ok(toast="Notitie toegevoegd.", note=_note_json(note))

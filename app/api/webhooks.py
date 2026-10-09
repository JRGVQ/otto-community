"""Provider push webhooks (CSRF-exempt; validated by provider signature instead)."""
from __future__ import annotations

from flask import Response, current_app, request

from app.api import api_bp
from app.extensions import csrf
from app.security.webhook_verify import verify_gmail_push, verify_graph_notification


@api_bp.route("/webhooks/gmail", methods=["POST"])
@csrf.exempt
def gmail_webhook():
    """Gmail realtime: a Pub/Sub push saying one mailbox has new history.

    Always answers 2xx once the request is authentic — a 4xx/5xx makes Pub/Sub retry
    with backoff and eventually dead-letter, and a mailbox we cannot resolve is not
    something a retry will fix. The push carries no mail content, only which address
    changed, so the sync itself does the real work.
    """
    import base64
    import binascii
    import json

    if not verify_gmail_push(request):
        return Response(status=202)  # ack & ignore — unverified or not configured

    from app.extensions import db
    from app.models import MailAccount
    from app.tasks.enqueue import enqueue
    from app.tasks.sync import sync_account

    envelope = request.get_json(silent=True) or {}
    raw = (envelope.get("message") or {}).get("data")
    if not raw:
        return Response(status=204)
    try:
        payload = json.loads(base64.b64decode(raw).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        current_app.logger.info("Gmail-push met onleesbare data genegeerd")
        return Response(status=204)

    address = (payload.get("emailAddress") or "").strip().lower()
    if not address:
        return Response(status=204)

    # One address can be connected by several users (each their own mailbox); sync each.
    accounts = (
        db.session.query(MailAccount)
        .filter(MailAccount.provider == "gmail", MailAccount.status == "connected")
        .filter(db.func.lower(MailAccount.email) == address)
        .all()
    )
    for account in accounts:
        enqueue(sync_account, account.id)
    if not accounts:
        current_app.logger.info("Gmail-push voor onbekende mailbox %s", address)
    return Response(status=204)


@api_bp.route("/webhooks/microsoft", methods=["POST"])
@csrf.exempt
def microsoft_webhook():
    # Graph subscription handshake: echo the validationToken.
    token = request.args.get("validationToken")
    if token:
        return Response(token, mimetype="text/plain")
    if not verify_graph_notification(request, current_app.config.get("GRAPH_CLIENT_STATE")):
        return Response(status=202)
    # Each notification → resolve the account by subscription id → enqueue a delta sync.
    from app.extensions import db
    from app.models import MailAccount
    from app.tasks.enqueue import enqueue
    from app.tasks.sync import sync_account

    payload = request.get_json(silent=True) or {}
    seen: set[str] = set()
    for item in payload.get("value", []):
        sub_id = item.get("subscriptionId")
        if not sub_id or sub_id in seen:
            continue
        seen.add(sub_id)
        account = db.session.query(MailAccount).filter_by(subscription_id=sub_id).first()
        if account:
            enqueue(sync_account, account.id)
    return Response(status=202)

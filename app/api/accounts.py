"""Mailbox account endpoints (connect IMAP, disconnect with erasure)."""
from __future__ import annotations

from flask import request
from flask_login import current_user, login_required

from app.api import api_bp
from app.api._helpers import ok
from app.extensions import db
from app.models import MailAccount
from app.security.crypto import encrypt


@api_bp.route("/accounts/imap", methods=["POST"])
@login_required
def connect_imap():
    from app.domains import normalize_email

    data = request.get_json(silent=True) or request.form
    # Typed by hand into the connect dialog, so it arrives with the same stray spaces as
    # a signup address — and here a bad address means the mailbox silently never syncs.
    email = normalize_email(data.get("email"))
    account = MailAccount(
        user_id=current_user.id, provider="imap", email=email,
        status="connected", last_sync="zojuist",
        provider_config={
            "host": (data.get("host") or "").strip(),
            "imap_port": data.get("imap_port", 993),
            "username": (data.get("username") or "").strip() or email,
            "password_enc": encrypt(data.get("password")),
        },
    )
    db.session.add(account)
    current_user.mailbox_count = (current_user.mailbox_count or 0) + 1
    db.session.commit()
    return ok(toast="IMAP-account gekoppeld")


@api_bp.route("/accounts/sync", methods=["POST"])
@login_required
def sync_now():
    """Pull new mail for the current user's mailboxes right now (manual refresh).

    Runs inline so the user gets an immediate result; the background scheduler keeps
    polling on the configured interval regardless.
    """
    from app.tasks.sync import sync_account

    # Include 'error' mailboxes too so a manual refresh retries a previously-failed one.
    accounts = (
        db.session.query(MailAccount)
        .filter(MailAccount.user_id == current_user.id,
                MailAccount.status.in_(("connected", "enabled", "error")))
        .all()
    )
    if not accounts:
        return ok(toast="Geen gekoppeld postvak om te synchroniseren.")
    new_count = sum(sync_account(a.id) for a in accounts)
    if new_count:
        return ok(reload=True, toast=f"{new_count} nieuwe mail(s) opgehaald.")
    # No new mail — distinguish 'truly up to date' from 'the sync errored' (now recorded).
    errored = (
        db.session.query(MailAccount)
        .filter(MailAccount.user_id == current_user.id, MailAccount.last_error.isnot(None))
        .first()
    )
    if errored:
        return ok(error=errored.last_error, toast=f"Synchroniseren mislukt — {errored.last_error}")
    return ok(toast="Postvak is bijgewerkt — geen nieuwe mail.")


def _spawn_recategorize(account_ids: list[int]) -> None:
    """Hercategoriseer op de achtergrond — tot 200 AI-aanroepen per postvak horen niet
    in een HTTP-request (zelfde afweging als _kick_initial_import bij het koppelen).
    Met een echte worker via enqueue; in eager-modus in een daemon-thread."""
    from flask import current_app

    from app.tasks.categorize import recategorize_account
    from app.tasks.enqueue import enqueue

    if not current_app.config.get("TASK_ALWAYS_EAGER", True):
        for aid in account_ids:
            enqueue(recategorize_account, aid)
        return

    import threading

    app = current_app._get_current_object()

    def _job() -> None:
        with app.app_context():
            for aid in account_ids:
                try:
                    recategorize_account(aid)
                except Exception:  # noqa: BLE001 — best-effort; fouten staan in het log
                    app.logger.exception("hercategorisatie mislukt voor postvak %s", aid)

    threading.Thread(target=_job, name="otto-recategorize", daemon=True).start()


@api_bp.route("/accounts/recategorize", methods=["POST"])
@login_required
def recategorize_now():
    """Herstel slecht binnengekomen mail (lege afzender/onderwerp/inhoud) en laat de AI
    het postvak daarna opnieuw categoriseren.

    Het herstel loopt inline (enkele Graph-calls, de gebruiker ziet meteen het aantal);
    de hercategorisatie zelf — potentieel honderden AI-aanroepen — start op de
    achtergrond. Handmatige indelingen (classified_by="user") blijven altijd staan."""
    from flask import current_app

    from app.tasks.sync import repair_message_details

    accounts = (
        db.session.query(MailAccount)
        .filter(MailAccount.user_id == current_user.id,
                MailAccount.status.in_(("connected", "enabled", "error")))
        .all()
    )
    if not accounts:
        return ok(toast="Geen gekoppeld postvak om te hercategoriseren.")
    repaired = 0
    for a in accounts:
        try:
            repaired += repair_message_details(a)
        except Exception as exc:  # noqa: BLE001 — herstel is best-effort, de rest gaat door
            db.session.rollback()
            current_app.logger.warning("herstel mislukt voor postvak %s: %s", a.id, exc)
    _spawn_recategorize([a.id for a in accounts])
    if repaired:
        return ok(reload=True,
                  toast=f"{repaired} mail(s) hersteld — hercategorisatie gestart, "
                        "ververs zo meteen voor het resultaat.")
    return ok(toast="Hercategorisatie gestart — ververs zo meteen voor het resultaat.")


@api_bp.route("/accounts/<int:account_id>/test", methods=["POST"])
@login_required
def test_account(account_id: int):
    """Live connection probe for ONE mailbox: actually reach the provider and report the
    real result — or its error code/message — instead of a blind 'OK'. Records the outcome
    on the account so Integraties shows the truth."""
    from app.providers import get_provider
    from app.providers.exceptions import ProviderAuthError
    from app.tasks.sync import describe_error

    account = db.session.get(MailAccount, account_id)
    if not account or account.user_id != current_user.id:
        return ok(error="onbekend"), 404
    try:
        provider = get_provider(account)
        if not provider.is_configured():
            return ok(connection_ok=False,
                      toast="Verbinding mislukt — de mailprovider is niet geconfigureerd (geen app-credentials of token).")
        recent = provider.list_recent(limit=1)  # a real authenticated read against the live mailbox
        account.last_error = None
        if account.status == "error":
            account.status = "connected"
        db.session.commit()
        seen = "mail aanwezig" if recent else "geen recente mail zichtbaar"
        return ok(connection_ok=True, reload=True,
                  toast=f"Verbinding OK — postvak bereikbaar ({seen}).")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        detail = describe_error(exc)
        acct = db.session.get(MailAccount, account_id)
        if acct is not None:
            acct.last_error = detail
            if isinstance(exc, ProviderAuthError):
                acct.status = "error"
            db.session.commit()
        return ok(connection_ok=False, reload=True, error=detail,
                  toast=f"Verbinding mislukt — {detail}")


@api_bp.route("/accounts/<int:account_id>", methods=["DELETE"])
@login_required
def disconnect(account_id: int):
    account = db.session.get(MailAccount, account_id)
    if not account or account.user_id != current_user.id:
        return ok(error="onbekend"), 404
    if account.is_shared:
        # Shared mailboxes are team-owned: use the leave flow so other members keep theirs.
        return ok(error="Gebruik 'verlaten' voor een gedeeld postvak."), 400
    # GDPR: cascade removes tokens + cached messages/drafts.
    db.session.delete(account)
    db.session.commit()
    return ok(toast="Account losgekoppeld — gegevens verwijderd")

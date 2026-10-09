"""OAuth helper utilities."""
from __future__ import annotations

from flask import current_app, url_for


def redirect_uri(endpoint: str) -> str:
    base = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    return base + url_for(endpoint)


def post_connect_target() -> str:
    """After a successful mailbox connect: the one-time intro the first time, else the
    inbox. Keeps the OAuth callbacks in step with the other onboarding-completion paths."""
    from flask_login import current_user

    seen = getattr(current_user, "intro_seen", True)
    return url_for("web.rondleiding") if not seen else url_for("web.inbox")


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def _existing_account(user_id: int, provider: str, email: str):
    """Het persoonlijke postvak dat deze gebruiker al voor dit adres heeft, of None.

    Adressen worden genormaliseerd vergeleken: Microsoft geeft de UPN soms met een
    hoofdletter terug, en dat mag geen tweede postvak opleveren. Gedeelde postvakken
    blijven buiten beschouwing — die horen bij een ander adres en een andere flow.
    """
    from app.extensions import db
    from app.models import MailAccount

    rows = (
        db.session.query(MailAccount)
        .filter_by(user_id=user_id, provider=provider)
        .order_by(MailAccount.id)
        .all()
    )
    wanted = _norm(email)
    return next((a for a in rows if not a.is_shared and _norm(a.email) == wanted), None)


def _mailbox_count(user_id: int) -> int:
    """Het werkelijke aantal persoonlijke postvakken van deze gebruiker."""
    from app.extensions import db
    from app.models import MailAccount

    rows = db.session.query(MailAccount).filter_by(user_id=user_id).all()
    return sum(1 for a in rows if not a.is_shared)


def finish_onboarding(provider: str, email: str, access_token: str, refresh_token: str | None,
                      expires_in: int | None = None):
    """Persist a connected account (tokens encrypted) and kick the initial import.

    Opnieuw koppelen is hetzelfde postvak, geen tweede. Dit maakte vroeger elke keer een
    nieuwe rij aan, dus wie twee keer op "Koppelen" klikte — of één keer opnieuw koppelde
    na een verlopen token — kreeg zijn adres twee, drie, vier keer in de lijst, elk met
    eigen tokens en een eigen sync. Zoeken op (gebruiker, provider, adres) en de
    bestaande rij bijwerken houdt de tokens, de berichten en de instellingen bij elkaar.
    """
    from datetime import timedelta

    from app.extensions import db
    from app.models import MailAccount
    from app.models.base import utcnow
    from app.security.crypto import encrypt
    from flask_login import current_user

    # Dubbels uit de tijd dat elke koppeling een nieuwe rij maakte, opruimen op het moment
    # dat de gebruiker toch al opnieuw koppelt: dan lost het scherm zichzelf op.
    from app.mailboxes import merge_duplicates

    merge_duplicates(current_user)
    account = _existing_account(current_user.id, provider, email)
    reconnect = account is not None
    if account is None:
        account = MailAccount(user_id=current_user.id, provider=provider, email=email)
        db.session.add(account)

    account.organization_id = current_user.organization_id
    account.status = "connected"
    account.access_token_enc = encrypt(access_token)
    account.refresh_token_enc = encrypt(refresh_token)
    # Persist expiry so the provider refreshes the token before the next poll
    # instead of polling fruitlessly with a stale token once the first hour is up.
    account.token_expiry = (utcnow() + timedelta(seconds=int(expires_in))) if expires_in else None
    account.last_sync = "zojuist"
    account.last_error = None  # een geslaagde koppeling wist de oude foutmelding
    # Geteld uit wat er écht staat: een losse teller liep bij elke herkoppeling op en
    # klopte daarna nooit meer.
    db.session.flush()
    current_user.mailbox_count = _mailbox_count(current_user.id)
    current_user.onboarded = True
    from app.events import record_event

    record_event(current_user.organization, "mailbox.connected",
                 detail=f"{provider} (opnieuw gekoppeld)" if reconnect else provider,
                 actor=(current_user.name or current_user.email))
    db.session.commit()
    _kick_initial_import(account.id)
    # Turn on realtime (Graph webhook) for this mailbox when enabled; harmless no-op otherwise.
    if provider == "outlook" and current_app.config.get("GRAPH_REALTIME"):
        try:
            from app.providers import get_provider

            get_provider(account).start_watch()
        except Exception as exc:  # noqa: BLE001 — never let watch setup break onboarding
            current_app.logger.info("start_watch on connect skipped: %s", exc)
    return account


def _kick_initial_import(account_id: int) -> None:
    """Start the first-connect mailbox import WITHOUT blocking the OAuth callback.

    With a Celery worker it's a normal enqueue. In the eager single-container mode there
    is no worker, so running it inline would import up to 300 messages AND create a draft
    for each — synchronously, during the HTTP callback — which times the request out (a
    500/502 right after the user accepts on Microsoft). Run it in a daemon thread instead
    so the callback returns immediately; `_persist` is idempotent, so a later scheduler
    sync can't double-import.
    """
    from app.tasks.enqueue import enqueue
    from app.tasks.sync import initial_import

    if not current_app.config.get("TASK_ALWAYS_EAGER", True):
        enqueue(initial_import, account_id)  # a real worker handles it off-request
        return

    import threading

    app = current_app._get_current_object()

    def _job() -> None:
        with app.app_context():
            try:
                initial_import(account_id)
            except Exception:  # noqa: BLE001 — best-effort; failures are logged, never user-facing
                app.logger.exception("async initial import failed for account %s", account_id)

    threading.Thread(target=_job, name=f"otto-import-{account_id}", daemon=True).start()

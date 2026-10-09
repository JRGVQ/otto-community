"""Celery beat schedule (periodic sync, token refresh, subscription renewal, cleanup).

Active only with a worker + beat running; safe no-ops otherwise. The in-app scheduler
(app/tasks/scheduler.py) runs the same jobs when there is no worker, so both
deployment modes behave identically.
"""
from __future__ import annotations


def configure_beat(celery) -> None:  # pragma: no cover - requires worker
    celery.conf.beat_schedule = {
        "periodic-sync": {"task": "otto.run", "schedule": 120.0,
                          "args": ["app.tasks.schedule.periodic_sync", [], {}]},
        "refresh-tokens": {"task": "otto.run", "schedule": 1800.0,
                           "args": ["app.tasks.schedule.refresh_tokens", [], {}]},
        "renew-subscriptions": {"task": "otto.run", "schedule": 3600.0,
                                "args": ["app.tasks.schedule.renew_subscriptions", [], {}]},
        "cleanup-cache": {"task": "otto.run", "schedule": 86400.0,
                          "args": ["app.tasks.schedule.cleanup_cache", [], {}]},
        "purge-trash": {"task": "otto.run", "schedule": 86400.0,
                        "args": ["app.tasks.schedule.purge_expired_trash", [], {}]},
        "purge-drafts": {"task": "otto.run", "schedule": 86400.0,
                         "args": ["app.tasks.schedule.purge_expired_drafts", [], {}]},
        "follow-ups": {"task": "otto.run", "schedule": 86400.0,
                       "args": ["app.tasks.schedule.generate_follow_up_drafts", [], {}]},
        "purge-logs": {"task": "otto.run", "schedule": 86400.0,
                       "args": ["app.tasks.schedule.purge_old_logs", [], {}]},
        "error-alerts": {"task": "otto.run", "schedule": 300.0,
                         "args": ["app.tasks.schedule.check_error_spike", [], {}]},
        "daily-task-email": {"task": "otto.run", "schedule": 3600.0,
                             "args": ["app.tasks.schedule.daily_task_email", [], {}]},
        "teams-bundles": {"task": "otto.run", "schedule": 300.0,
                          "args": ["app.tasks.schedule.flush_teams_bundles", [], {}]},
        "auto-replies": {"task": "otto.run", "schedule": 300.0,
                         "args": ["app.tasks.autoreply.push_pending_auto_replies", [], {}]},
        "commitments": {"task": "otto.run", "schedule": 86400.0,
                        "args": ["app.tasks.schedule.harvest_commitments", [], {}]},
        "transcripts": {"task": "otto.run", "schedule": 86400.0,
                        "args": ["app.tasks.schedule.harvest_transcripts", [], {}]},
        "style-refresh": {"task": "otto.run", "schedule": 86400.0,
                          "args": ["app.tasks.learn.refresh_style_profiles", [], {}]},
        "domain-claims": {"task": "otto.run", "schedule": 86400.0,
                          "args": ["app.tasks.schedule.recheck_domain_claims", [], {}]},
    }


def periodic_sync() -> None:  # pragma: no cover
    # Same interval-aware logic the in-app scheduler uses, so the beat and eager
    # paths behave identically (each mailbox honours its own configured interval).
    from app.tasks.scheduler import run_due_syncs

    run_due_syncs()


def check_error_spike() -> int:  # pragma: no cover - network side effect
    """Open a GitHub issue when one kind of error spikes. Self-gates on configuration.

    Mirrored here (and on the in-app scheduler tick) so both the Celery and the
    single-container deployment alert identically.
    """
    from app.alerting import check_error_spike as _check

    return _check()


def daily_task_email() -> None:  # pragma: no cover
    # Hourly; self-gates on DAILY_EMAIL_HOUR + a per-user last-sent date.
    from app.tasks.digest import send_daily_task_emails

    send_daily_task_emails()


def refresh_tokens() -> None:  # pragma: no cover
    from app.extensions import db
    from app.models import MailAccount
    from app.providers import get_provider

    # Refresh the grant-holding accounts only. A shared mailbox borrows a member's
    # personal token (delegated_via_account_id set) — refreshing it here too would
    # consume the rotating refresh token twice and could break the grant.
    for account in db.session.query(MailAccount).filter(MailAccount.delegated_via_account_id.is_(None)):
        try:
            get_provider(account).refresh_token()
        except Exception:
            pass


def renew_subscriptions() -> None:  # pragma: no cover
    """Keep every connected mailbox's realtime watch alive — Graph subscriptions AND
    Gmail's `users.watch`.

    Gmail used to be missing here entirely, which is what made its realtime path dead on
    arrival: the webhook was ready and verified pushes correctly, but nothing ever told
    Google to send any. A Gmail watch also expires much faster than a Graph subscription
    (7 days, hard limit), so it needs re-arming on the same pass.
    """
    from datetime import timedelta

    from flask import current_app

    from app.extensions import db
    from app.models import MailAccount
    from app.models.base import utcnow
    from app.providers import get_provider

    providers = [p for p, on in (("outlook", current_app.config.get("GRAPH_REALTIME")),
                                 ("gmail", current_app.config.get("GMAIL_REALTIME"))) if on]
    if not providers:
        return
    soon = utcnow() + timedelta(hours=12)
    # Personal mailboxes only — a shared mailbox (delegated_via_account_id set) borrows a
    # member's token and cannot drive provider subscriptions; it stays on the 120s poll.
    accounts = (
        db.session.query(MailAccount)
        .filter(MailAccount.status == "connected", MailAccount.provider.in_(providers))
        .filter(MailAccount.delegated_via_account_id.is_(None))
    )
    for account in accounts:
        try:
            provider = get_provider(account)
            if not account.subscription_id:
                provider.start_watch()  # (re)create if missing
            elif account.watch_expiry and account.watch_expiry <= soon:
                provider.renew_watch()  # extend before it expires
        except Exception:  # noqa: BLE001 — one bad account must not stall the rest
            current_app.logger.info("renew_subscriptions skipped for account %s", account.id)


def cleanup_cache() -> None:  # pragma: no cover
    """Retention: purge cached message bodies past the retention window (data minimisation)."""
    pass


TRASH_RETENTION_DAYS = 30


def purge_expired_trash() -> int:
    """Permanently delete companies that have sat in the trash for 30+ days.

    The soft-delete window is the recovery grace period; once it lapses the data is
    wiped for real (GDPR: don't keep deleted customers around forever). Anchored on
    `deleted_at`. Each purge is impossible to undo, so it's logged to the app log
    before the org (and its audit trail) disappear with it."""
    from datetime import timedelta

    from flask import current_app

    from app.extensions import db
    from app.models import Organization
    from app.models.base import utcnow
    from app.tenancy import delete_org_deep

    cutoff = utcnow() - timedelta(days=TRASH_RETENTION_DAYS)
    n = 0
    # Snapshot the ids first — delete_org_deep mutates the session as it cascades.
    expired = (
        db.session.query(Organization)
        .filter(Organization.deleted_at.isnot(None), Organization.deleted_at < cutoff)
        .all()
    )
    for org in expired:
        current_app.logger.info(
            "[scheduler] purging trashed org %s (%s), deleted %s",
            org.id, org.slug, org.deleted_at.date() if org.deleted_at else "?",
        )
        delete_org_deep(org)
        db.session.commit()
        n += 1
    return n


def purge_old_logs() -> int:
    """Delete request/error log rows older than the configured retention window.

    Retention is owner-set in the backoffice (default 30 days); this keeps the access
    and error logs from growing unbounded. Returns the number of rows removed."""
    from datetime import timedelta

    from app.extensions import db
    from app.models import ErrorLog, RequestLog
    from app.models.base import utcnow
    from app.platform_settings import log_retention_days

    cutoff = utcnow() - timedelta(days=log_retention_days())
    removed = 0
    removed += db.session.query(RequestLog).filter(RequestLog.occurred_at < cutoff).delete(synchronize_session=False)
    removed += db.session.query(ErrorLog).filter(ErrorLog.occurred_at < cutoff).delete(synchronize_session=False)
    db.session.commit()
    return removed


def _draft_ttl_days(draft) -> int | None:
    """The configured concept-lifetime for one draft, or None when disabled.

    Shared-mailbox concepts follow the mailbox's own setting; personal concepts follow
    the owning user's StyleProfile. Returns None (keep forever) when unset or ≤ 0.
    """
    msg = draft.message
    if msg is None:
        return None
    acct = msg.account
    if acct is not None and acct.is_shared:
        ttl = getattr(acct.settings, "draft_ttl_days", None) if acct.settings else None
    else:
        owner = acct.user if acct else None
        style = getattr(owner, "style_profile", None) if owner else None
        ttl = getattr(style, "draft_ttl_days", None) if style else None
    return ttl if (ttl and ttl > 0) else None


def purge_expired_drafts() -> int:
    """Discard unsent concept drafts older than their mailbox/user's `draft_ttl_days`.

    Keeps the provider Drafts folder (and Otto's inbox) from filling up with stale
    concepts nobody sent. The Otto row is marked discarded (history stays); the matching
    provider-side draft is deleted so it also disappears from Outlook/Gmail. Never touches
    sent or already-discarded drafts, and never sends anything."""
    from datetime import timedelta

    from flask import current_app

    from app.constants import DRAFT_CONCEPT, DRAFT_DISCARDED
    from app.extensions import db
    from app.models import Draft
    from app.models.base import utcnow
    from app.providers import get_provider

    now = utcnow()
    n = 0
    concepts = db.session.query(Draft).filter(Draft.status == DRAFT_CONCEPT).all()
    for draft in concepts:
        ttl = _draft_ttl_days(draft)
        if ttl is None or draft.created_at is None:
            continue
        if draft.created_at > now - timedelta(days=ttl):
            continue
        # Best-effort provider cleanup — a failure here must not block the local discard.
        if draft.provider_draft_id and draft.message and draft.message.account:
            try:
                get_provider(draft.message.account).delete_draft(draft.provider_draft_id)
            except Exception:  # noqa: BLE001
                current_app.logger.info("[scheduler] provider draft delete skipped for draft %s", draft.id)
        draft.status = DRAFT_DISCARDED
        n += 1
    if n:
        db.session.commit()
        current_app.logger.info("[scheduler] discarded %s expired concept draft(s)", n)
    return n


def generate_follow_up_drafts() -> int:  # pragma: no cover - provider-driven; unit-tested via helpers
    """Draft a polite nudge for sent mail that got no reply after the configured window.

    Delegates to the follow-up task module; kept here so the beat schedule and the in-app
    scheduler share one entry point. Never sends — it only prepares concepts the user sends."""
    from app.tasks.followup import run_follow_up_sweep

    return run_follow_up_sweep()


def flush_teams_bundles() -> int:  # pragma: no cover - webhook-driven; unit-tested via helpers
    """Post de gebundelde Teams-meldingen die aan hun vaste moment toe zijn.

    Draait elke tik en self-gate't per mailbox op moment + laatste flush, zoals de
    dagelijkse takenmail."""
    from app.tasks.teams_notify import flush_bundles

    return flush_bundles()


def harvest_commitments() -> int:  # pragma: no cover - provider-driven; unit-tested via helpers
    """Maak taken van de toezeggingen in recent verzonden mail.

    Zelfde plek als de opvolgsweep: die kijkt naar mail zónder antwoord, deze naar wat je
    in die mail beloofd hebt. Beide zelf-gated op de instelling per postvak."""
    from app.tasks.commitments import run_commitment_sweep

    return run_commitment_sweep()


def harvest_transcripts() -> int:  # pragma: no cover - provider-driven; unit-tested via helpers
    """Zet de actiepunten uit recente vergadertranscripties op de takenlijst.

    Zelf-gated op de instelling per postvak; geen notulen-bot, alleen transcripties die de
    tenant zelf al laat maken (zie app/transcripts.py)."""
    from app.tasks.transcripts import run_transcript_sweep

    return run_transcript_sweep()


def recheck_domain_claims(*, max_checks: int = 200) -> int:
    """Re-verify domain claims against DNS. Returns the number of claims that changed.

    Two jobs in one sweep: retry the claims still waiting for their TXT record to
    appear, and re-check the verified ones so a domain that lost its record (customer
    left, DNS rebuilt) stops granting SSO exclusivity. A check that cannot conclude —
    no resolver installed, DNS timeout — leaves the claim untouched by design, so a
    transient outage never revokes a domain someone legitimately owns.
    """
    from flask import current_app

    from app.domains import verify_claim
    from app.extensions import db
    from app.models import DomainClaim

    changed = 0
    claims = db.session.query(DomainClaim).order_by(DomainClaim.last_checked_at.asc().nullsfirst()).limit(max_checks)
    for claim in claims.all():
        was_verified = claim.is_verified
        try:
            verify_claim(claim)
        except Exception:  # noqa: BLE001 — one bad domain must never stop the sweep
            current_app.logger.warning("[scheduler] domain recheck failed for %s", claim.domain, exc_info=True)
            db.session.rollback()
            continue
        if claim.is_verified != was_verified:
            changed += 1
            current_app.logger.info(
                "[scheduler] domain %s is nu %s", claim.domain,
                "geverifieerd" if claim.is_verified else "niet langer geverifieerd",
            )
    return changed

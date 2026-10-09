"""Operationeel: foutenconsole, verkeerslog, gebeurtenissen, systeemstatus en scheduler."""
from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.extensions import db
from app.models import ErrorLog, MailAccount
from app.models.base import utcnow
from app.owner import owner_bp
from app.owner.routes._shared import _ctx
from datetime import timedelta


# ── Debug & errors console ───────────────────────────────────────────────────
@owner_bp.route("/debug")
def debug():
    status = request.args.get("status", type=int)
    q = db.session.query(ErrorLog).order_by(ErrorLog.occurred_at.desc())
    if status:
        q = q.filter(ErrorLog.status_code == status)
    errors = q.limit(200).all()
    total = db.session.query(ErrorLog).count()
    from app.alerting import is_enabled

    alerting = {
        "enabled": is_enabled(),
        "repo": current_app.config.get("GITHUB_ISSUE_REPO"),
        "threshold": current_app.config.get("ERROR_ALERT_THRESHOLD", 10),
        "window": current_app.config.get("ERROR_ALERT_WINDOW_MINUTES", 15),
    }
    return render_template("owner/debug.html", errors=errors, total=total, filter_status=status,
                           alerting=alerting, **_ctx("debug"))


@owner_bp.route("/alerts/check", methods=["POST"])
def alerts_check():
    """Run the error-spike check now instead of waiting for the scheduler tick."""
    from app.alerting import check_error_spike

    n = check_error_spike()
    if n:
        flash(_("%(n)s GitHub-issue(s) aangemaakt.", n=n), "toast")
    else:
        flash(_("Geen foutenpiek gevonden — geen issue aangemaakt."), "toast")
    return redirect(url_for("owner.debug"))


@owner_bp.route("/debug/clear", methods=["POST"])
def debug_clear():
    db.session.query(ErrorLog).delete()
    db.session.commit()
    flash(_("Foutenlog gewist"), "toast")
    return redirect(url_for("owner.debug"))


@owner_bp.route("/debug/test-error")
def debug_test_error():
    raise RuntimeError("Test-fout vanuit de debug-console — dit is opzettelijk.")


# ── Logboek: full request access log across every environment ─────────────────
@owner_bp.route("/logs")
def logs():
    from app.models import RequestLog
    from app.platform_settings import log_retention_days

    kind = request.args.get("kind")           # errors | client | slow | None (all)
    q_path = (request.args.get("q") or "").strip()
    q = db.session.query(RequestLog).order_by(RequestLog.occurred_at.desc())
    if kind == "errors":
        q = q.filter(RequestLog.status_code >= 500)
    elif kind == "client":
        q = q.filter(RequestLog.status_code >= 400, RequestLog.status_code < 500)
    elif kind == "slow":
        q = q.filter(RequestLog.duration_ms >= 2000)
    if q_path:
        q = q.filter(RequestLog.path.ilike(f"%{q_path}%"))
    rows = q.limit(300).all()
    total = db.session.query(RequestLog).count()
    return render_template(
        "owner/logs.html", rows=rows, total=total, kind=kind, q_path=q_path,
        retention_days=log_retention_days(), **_ctx("logs"),
    )


@owner_bp.route("/logs/retention", methods=["POST"])
def logs_retention():
    from app.platform_settings import set_log_retention_days

    try:
        days = int(request.form.get("days", "30"))
    except (TypeError, ValueError):
        flash(_("Ongeldig aantal dagen."), "error")
        return redirect(url_for("owner.logs"))
    stored = set_log_retention_days(days)
    flash(_("Bewaartermijn ingesteld op %(days)s dagen.", days=stored), "toast")
    return redirect(url_for("owner.logs"))


@owner_bp.route("/logs/clear", methods=["POST"])
def logs_clear():
    from app.models import RequestLog

    db.session.query(RequestLog).delete()
    db.session.commit()
    flash(_("Verkeerslog gewist"), "toast")
    return redirect(url_for("owner.logs"))


@owner_bp.route("/logs/prune", methods=["POST"])
def logs_prune():
    """Apply the retention window right now (don't wait for the daily scheduler)."""
    from app.tasks.schedule import purge_old_logs

    gone = purge_old_logs()
    flash(_("%(n)s oude regels opgeruimd.", n=gone), "toast")
    return redirect(url_for("owner.logs"))


# ── Scheduler & mailbox-sync check ───────────────────────────────────────────
def _scheduler_state() -> dict:
    """Live view of the in-app poller and every mailbox's sync health.

    The authoritative 'is the poller alive?' signal is the leader lease in
    scheduler_locks: the leader renews it every tick, so a lease that is still in
    the future means a worker polled within the last lease window. BACKGROUND_SYNC
    being off (the classic 'Redis set, no worker' footgun) means no poller runs at
    all — surfaced separately with the exact env-var remedy.
    """
    from app.models.scheduler import SchedulerLock
    from app.tasks.scheduler import _LOCK_NAME, due_accounts
    from app.timefmt import reltime

    cfg = current_app.config
    now = utcnow()
    tick = max(int(cfg.get("SCHEDULER_TICK_SECONDS", 60)), 15)
    lease = tick * 3

    lock = db.session.get(SchedulerLock, _LOCK_NAME)
    last_beat = (lock.expires_at - timedelta(seconds=lease)) if (lock and lock.expires_at) else None
    beat_age = int((now - last_beat).total_seconds()) if last_beat else None
    # Alive = the lease has not yet expired (a worker renewed it within the last lease).
    poller_alive = bool(lock and lock.expires_at and lock.expires_at > now)

    bg_on = bool(cfg.get("BACKGROUND_SYNC"))
    realtime = bool(cfg.get("GRAPH_REALTIME"))
    redis_set = bool(cfg.get("REDIS_URL"))

    # Verdict: worst problem first.
    if not bg_on and not realtime:
        verdict = ("error", "Achtergrond-sync staat UIT en er is geen realtime webhook — "
                   "nieuwe mail wordt nooit opgehaald.")
    elif bg_on and not poller_alive:
        verdict = ("error", "Achtergrond-sync staat aan, maar de poller heeft geen recente "
                   "hartslag — de thread draait niet.")
    elif not bg_on and realtime:
        verdict = ("warn", "Achtergrond-sync staat uit; sync verloopt via de realtime webhook.")
    else:
        verdict = ("ok", "De poller draait en haalt mailboxen op hun interval op.")

    due = {a.id for a in due_accounts(now)}
    rows = []
    for a in db.session.query(MailAccount).order_by(MailAccount.id):
        org = a.organization or (a.user.organization if a.user else None)
        rows.append({
            "email": a.email or a.target_address or "—",
            "provider": a.provider,
            "org": org.name if org else "—",
            "status": a.status,
            "shared": a.is_shared,
            "last_sync_at": a.last_sync_at,
            "last_sync_rel": reltime(a.last_sync_at, "nooit"),
            "interval": a.sync_interval_seconds or cfg.get("SYNC_INTERVAL_SECONDS", 300),
            "due": a.id in due,
            "last_error": a.last_error,
        })

    config_rows = [
        ("BACKGROUND_SYNC", "aan" if bg_on else "UIT",
         bg_on, "In-app poller-thread. Zet op 1 als Redis draait maar er geen Celery-worker is."),
        ("TASK_ALWAYS_EAGER", "aan" if cfg.get("TASK_ALWAYS_EAGER") else "uit",
         None, "Pipeline draait inline i.p.v. via een worker."),
        ("REDIS_URL", "gezet" if redis_set else "niet gezet",
         None, "Als dit gezet is maar geen worker draait, valt de sync stil tenzij BACKGROUND_SYNC=1."),
        ("GRAPH_REALTIME", "aan" if realtime else "uit",
         None, "Realtime webhook-sync (optioneel alternatief voor de poller)."),
        ("SCHEDULER_TICK_SECONDS", str(tick),
         None, "Hoe vaak de poller wakker wordt."),
        ("SYNC_INTERVAL_SECONDS", str(cfg.get("SYNC_INTERVAL_SECONDS", 300)),
         None, "Standaard poll-cadans per mailbox."),
    ]

    return {
        "verdict": verdict, "poller_alive": poller_alive, "bg_on": bg_on,
        "lock_holder": lock.holder if lock else None,
        "last_beat": last_beat, "beat_age": beat_age, "lease": lease,
        "rows": rows, "config_rows": config_rows,
        "connected": sum(1 for r in rows if r["status"] == "connected"),
        "errored": sum(1 for r in rows if r["status"] == "error"),
        "due_now": sum(1 for r in rows if r["due"]),
    }


@owner_bp.route("/scheduler")
def scheduler():
    return render_template("owner/scheduler.html", **_scheduler_state(), **_ctx("scheduler"))


@owner_bp.route("/scheduler/sync", methods=["POST"])
def scheduler_sync():
    """Force an immediate sync of every mailbox — the manual 'kick' when the poller
    stalled or a mailbox is behind. Bypasses the due-interval filter but stays
    idempotent (each provider fetches only from its own cursor)."""
    from app.tasks.enqueue import enqueue
    from app.tasks.sync import sync_account

    accounts = db.session.query(MailAccount).filter(
        MailAccount.status.in_(("connected", "enabled", "error"))
    ).all()
    for a in accounts:
        a.last_sync_at = utcnow()  # claim, mirroring run_due_syncs
    db.session.commit()
    for a in accounts:
        enqueue(sync_account, a.id)
    flash(f"Sync gestart voor {len(accounts)} mailbox(en). Ververs voor het resultaat.", "toast")
    return redirect(url_for("owner.scheduler"))



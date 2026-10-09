"""In-app periodic mailbox sync — the polling loop that keeps inboxes fresh.

Otto's pipeline (sync → categorize → draft) is normally driven either by a Graph
webhook (realtime, opt-in) or by Celery beat (`periodic_sync`, needs Redis). The
default single-container deployment has neither, so a freshly connected mailbox
was imported once and then never polled again.

This module fills that gap with a lightweight daemon thread: every tick it elects
a single leader (so Gunicorn's worker fleet doesn't sync each mailbox N times) and
enqueues a sync for every account whose own interval has elapsed. In eager mode
`enqueue` runs the sync inline; with a real worker it hands off to Celery. Either
way the cadence is the same, and each account's interval is user-configurable.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import MailAccount
from app.models.base import utcnow
from app.models.scheduler import acquire_lock
from app.tasks.digest import send_daily_task_emails
from app.tasks.enqueue import enqueue
from app.tasks.schedule import (
    flush_teams_bundles,
    generate_follow_up_drafts,
    harvest_commitments,
    harvest_transcripts,
    purge_expired_drafts,
    purge_expired_trash,
    purge_old_logs,
    recheck_domain_claims,
    renew_subscriptions,
)
from app.tasks.sync import sync_account

_LOCK_NAME = "mailbox-sync"
_started = False


def _interval_for(account: MailAccount) -> int:
    """Effective poll interval for one mailbox, honouring the configured floor."""
    cfg = current_app.config
    default = cfg.get("SYNC_INTERVAL_SECONDS", 300)
    floor = cfg.get("SYNC_MIN_INTERVAL_SECONDS", 60)
    return max(account.sync_interval_seconds or default, floor)


def due_accounts(now=None) -> list[MailAccount]:
    """Connected mailboxes whose interval has elapsed since their last sync.

    Demo/seed mailboxes are excluded: they carry mock mail and no provider grant, so
    polling them only ever logs an auth failure (every demo visit creates one, so the
    fleet of them was drowning the log in "Geen geldige Graph-token" warnings).
    """
    now = now or utcnow()
    out = []
    for account in db.session.query(MailAccount).filter_by(status="connected"):
        if account.is_mock:
            continue
        last = account.last_sync_at
        if last is None or last <= now - timedelta(seconds=_interval_for(account)):
            out.append(account)
    return out


def run_due_syncs(now=None) -> int:
    """Enqueue a sync for every due mailbox; return how many were triggered.

    The due timestamp is *claimed* (written before enqueue) so a second tick — or a
    second worker that briefly races the lock — won't enqueue the same mailbox twice
    while the first sync is still in flight.
    """
    now = now or utcnow()
    triggered = 0
    for account in due_accounts(now):
        account.last_sync_at = now  # claim before handing off
        db.session.commit()
        enqueue(sync_account, account.id)
        triggered += 1
    return triggered


def start_background_sync(app) -> None:
    """Spawn the poller thread once, when this deployment has no external scheduler.

    Idempotent and safe to call on every request — it is wired to the first request
    rather than app construction so it never starts in the Werkzeug reloader's parent
    process (which loads the app but serves nothing). Each serving process — the
    reloaded dev child, a bare `flask run`, or every Gunicorn worker — starts exactly
    one thread; the leader lock keeps the fleet down to a single active poller.
    """
    global _started
    if _started or not app.config.get("BACKGROUND_SYNC") or app.config.get("TESTING"):
        return

    _started = True
    holder = f"{socket.gethostname()}:{os.getpid()}"
    tick = max(int(app.config.get("SCHEDULER_TICK_SECONDS", 60)), 15)
    lease = tick * 3  # outlive a couple of missed ticks before another worker steals it
    _daily = {"date": None}  # gate once-a-day maintenance to one run per calendar day
    _hourly = {"hour": None}  # gate hourly maintenance (realtime watches)

    def loop() -> None:
        time.sleep(min(tick, 10))  # let boot/migrations settle before the first poll
        while True:
            try:
                with app.app_context():
                    if acquire_lock(_LOCK_NAME, ttl_seconds=lease, holder=holder):
                        # ELKE taak draait geïsoleerd. Eerder zat de hele tick in één try:
                        # struikelde de eerste taak, dan werden alerting én alle dagelijkse
                        # taken overgeslagen — precies wanneer je ze het hardst nodig hebt.
                        def _safe(naam, fn, eenheid=None):
                            try:
                                uitkomst = fn()
                            except Exception:  # noqa: BLE001 — één taak mag de tick niet slopen
                                db.session.rollback()
                                app.logger.exception("[scheduler] taak '%s' mislukt", naam)
                                return None
                            if uitkomst and eenheid:
                                app.logger.info("[scheduler] %s %s", uitkomst, eenheid)
                            return uitkomst

                        # Alerting eerst: een storing elders mag de alarmering niet wegdrukken.
                        # Self-gates on configuration; own try/except inside.
                        from app.alerting import check_error_spike

                        _safe("alerting", check_error_spike)
                        _safe("mailbox-sync", run_due_syncs, "mailbox(es) gesynchroniseerd")
                        # Daily task digests (self-gates on hour + per-user date).
                        _safe("dagelijkse takenmail", send_daily_task_emails, "takenmail(s) verstuurd")
                        # Gebundelde Teams-meldingen op hun vaste momenten (self-gated).
                        _safe("Teams-bundels", flush_teams_bundles, "Teams-bundel(s) verstuurd")
                        # Openstaande afwezigheidsinstellingen naar Exchange duwen (alleen
                        # 'pending' rijen → goedkoop). Exchange verstuurt het OOO, niet Otto.
                        from app.tasks.autoreply import push_pending_auto_replies

                        _safe("afwezigheid", push_pending_auto_replies, "afwezigheidsinstelling(en) gepusht")
                        # Once an hour: (re-)arm realtime watches — Gmail's users.watch
                        # expires after 7 days. Self-gates on configuration. (Tokens refresh
                        # themselves on a 401, so no separate token job is needed here.)
                        hour = utcnow().replace(minute=0, second=0, microsecond=0)
                        if _hourly["hour"] != hour:
                            _hourly["hour"] = hour
                            _safe("realtime-abonnementen", renew_subscriptions)
                        # Once a day: permanently purge anything sitting in the trash for
                        # 30+ days, then the other daily maintenance.
                        today = utcnow().date()
                        if _daily["date"] != today:
                            _daily["date"] = today
                            _safe("prullenbak legen", purge_expired_trash, "org(s) definitief gewist")
                            # Discard stale unsent concepts, then draft follow-ups for
                            # unanswered sent mail. Both self-gate on per-mailbox settings.
                            _safe("oude concepten opruimen", purge_expired_drafts)
                            _safe("opvolgingen", generate_follow_up_drafts)
                            # En de andere kant van verzonden mail: wat je zélf beloofd hebt.
                            _safe("toezeggingen", harvest_commitments)
                            # Actiepunten uit vergadertranscripties op de takenlijst zetten.
                            _safe("transcripties", harvest_transcripts)
                            # Schrijfstijl bijwerken uit de écht verzonden mail. Stond
                            # alleen achter de handmatige knop, dus veranderde nooit mee.
                            from app.tasks.learn import refresh_style_profiles

                            _safe("schrijfstijl", refresh_style_profiles, "stijlprofiel(en) ververst")
                            # Trim request/error logs past the retention window.
                            _safe("logs opschonen", purge_old_logs, "oude logregel(s) gewist")
                            # Re-check claimed domains: pending ones may now resolve,
                            # verified ones may have lost their TXT record.
                            _safe("domeinclaims", recheck_domain_claims, "domeinclaim(s) gewijzigd")
            except Exception:  # noqa: BLE001 — a bad tick must never kill the loop
                app.logger.exception("[scheduler] tick failed")
            time.sleep(tick)

    threading.Thread(target=loop, name="otto-sync", daemon=True).start()
    app.logger.info("[scheduler] background mailbox sync started (tick=%ss)", tick)

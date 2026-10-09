"""Per-organisation background-queue depth.

Celery's broker queue isn't tagged by tenant, so to keep the Beheer overview's
"Taken in wachtrij" strictly per-org (one company must never see another's load,
not even an aggregate) we keep a tiny per-org counter in Redis: enqueue()
increments it and the worker decrements it the moment the task STARTS — so it
counts tasks that are still waiting, not ones already running. Every operation
degrades to a no-op without Redis (eager mode → always 0), and never raises, so
the metric can never affect task processing.
"""
from __future__ import annotations

from flask import current_app


def _redis():
    cfg = current_app.config
    if cfg.get("TASK_ALWAYS_EAGER", True) or not cfg.get("REDIS_URL"):
        return None
    try:
        import redis

        return redis.from_url(cfg["REDIS_URL"])
    except Exception:  # noqa: BLE001 - no redis client / bad URL → metric simply absent
        return None


def _key(org_id: int) -> str:
    return f"otto:queue:org:{org_id}"


def task_org_id(func, args) -> int | None:
    """Best-effort organisation id for a queued pipeline task, from its first arg.
    Maintenance/beat tasks (no org) return None and are not counted per-org."""
    from app.extensions import db

    name = getattr(func, "__name__", "")
    try:
        if name == "process_incoming" and args:
            from app.models import Message

            msg = db.session.get(Message, args[0])
            return msg.organization_id if msg else None
        if name in ("sync_account", "initial_import") and args:
            from app.models import MailAccount

            acct = db.session.get(MailAccount, args[0])
            if not acct:
                return None
            return acct.organization_id or (acct.user.organization_id if acct.user else None)
    except Exception:  # noqa: BLE001
        return None
    return None


def incr(org_id: int | None) -> None:
    if not org_id:
        return
    r = _redis()
    if r is None:
        return
    try:
        r.incr(_key(org_id))
    except Exception:  # noqa: BLE001
        pass


def decr(org_id: int | None) -> None:
    if not org_id:
        return
    r = _redis()
    if r is None:
        return
    try:
        if r.decr(_key(org_id)) < 0:  # clamp: never report a negative backlog
            r.set(_key(org_id), 0)
    except Exception:  # noqa: BLE001
        pass


def pending(org_id: int) -> int:
    """Waiting background tasks for this org (0 without Redis / on any error)."""
    r = _redis()
    if r is None:
        return 0
    try:
        val = r.get(_key(org_id))
        return max(0, int(val)) if val is not None else 0
    except Exception:  # noqa: BLE001
        return 0

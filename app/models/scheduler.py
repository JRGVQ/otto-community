"""Cross-process leader election for the in-app background scheduler.

The single-container deployment runs Gunicorn with several workers, so every
worker process would otherwise spawn its own poller and sync each mailbox N
times (and double-rotate refresh tokens). A short-lived row-level lease elects
one worker as the leader each tick; the rest idle. Works on SQLite and Postgres,
and across containers, with no Redis.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, update

from app.extensions import db
from app.models.base import utcnow


class SchedulerLock(db.Model):
    __tablename__ = "scheduler_locks"

    name = db.Column(db.String(64), primary_key=True)
    holder = db.Column(db.String(96))      # "<host>:<pid>" of the worker that owns the lease
    expires_at = db.Column(db.DateTime)    # lease expiry; a stale lease may be stolen


def acquire_lock(name: str, ttl_seconds: int, holder: str) -> bool:
    """Atomically claim/renew the named lease for `holder`. Returns True if held.

    A single conditional UPDATE does the election: it only succeeds when the lease
    is free, expired, or already ours — so concurrent workers can never both win.
    """
    now = utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    res = db.session.execute(
        update(SchedulerLock)
        .where(SchedulerLock.name == name)
        .where(or_(
            SchedulerLock.expires_at.is_(None),
            SchedulerLock.expires_at < now,
            SchedulerLock.holder == holder,
        ))
        .values(holder=holder, expires_at=expires)
    )
    if res.rowcount:
        db.session.commit()
        return True

    # No row updated: either it doesn't exist yet, or another worker holds a live lease.
    if db.session.get(SchedulerLock, name) is None:
        db.session.add(SchedulerLock(name=name, holder=holder, expires_at=expires))
        try:
            db.session.commit()
            return True
        except Exception:  # noqa: BLE001 — lost the insert race; someone else is leader
            db.session.rollback()
            return False
    db.session.rollback()
    return False

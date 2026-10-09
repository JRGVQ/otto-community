"""The single switch between inline (eager) and Celery execution.

Every caller uses `enqueue(func, *args)` rather than touching Celery directly, so
with no REDIS_URL set the whole pipeline runs inline inside the request — no worker
needed — and the demo works out of the box.
"""
from __future__ import annotations

from flask import current_app


def _dotted(func) -> str:
    return f"{func.__module__}.{func.__name__}"


def enqueue(func, *args, **kwargs):
    if current_app.config.get("TASK_ALWAYS_EAGER", True):
        return func(*args, **kwargs)

    # Celery path (only reached when REDIS_URL is configured).
    from app.tasks import queue_meter
    from app.tasks.celery_app import get_celery

    # Count this task against its org's waiting-queue depth; the worker decrements
    # it when the task starts (see otto.run). Per-org so the Beheer overview never
    # shows one tenant another tenant's load.
    org_id = queue_meter.task_org_id(func, args)
    queue_meter.incr(org_id)
    celery = get_celery(current_app._get_current_object())
    return celery.send_task(
        "otto.run", args=[_dotted(func), list(args), kwargs], kwargs={"_org_id": org_id}
    )

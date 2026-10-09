"""Celery wiring (lazy — never imported in eager mode).

A single generic `otto.run` task dispatches any pipeline function by dotted path,
so individual tasks need no per-function registration. Beat schedule lives in
app.tasks.schedule.
"""
from __future__ import annotations

_celery = None


def get_celery(app):
    global _celery
    if _celery is not None:
        return _celery

    from celery import Celery
    from werkzeug.utils import import_string

    _celery = Celery(
        "otto",
        broker=app.config["REDIS_URL"],
        backend=app.config["REDIS_URL"],
    )
    _celery.conf.task_always_eager = app.config.get("TASK_ALWAYS_EAGER", False)

    @_celery.task(name="otto.run")
    def run(dotted, args, kwargs, _org_id=None):  # pragma: no cover - exercised only with a worker
        with app.app_context():
            from app.tasks import queue_meter

            queue_meter.decr(_org_id)  # no longer "waiting" once the worker starts it
            func = import_string(dotted)
            return func(*(args or []), **(kwargs or {}))

    from app.tasks.schedule import configure_beat

    configure_beat(_celery)
    return _celery

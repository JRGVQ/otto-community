"""Celery worker entrypoint (used only when REDIS_URL is configured).

    celery -A worker.celery worker --loglevel=info --pool=solo   # --pool=solo avoids macOS fork issues
    celery -A worker.celery beat --loglevel=info
"""
from app import create_app
from app.tasks.celery_app import get_celery

app = create_app()
celery = get_celery(app)

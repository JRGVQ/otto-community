# Otto production image. Python 3.12 (rock-solid wheels for celery/psycopg/cryptography).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

# App code.
COPY . .
# Compile the gettext catalogs (.po → .mo) so translations load in prod even if a
# committed .mo ever drifts from its .po. pybabel ships with Flask-Babel (in requirements).
RUN pybabel compile -d app/translations || true
RUN chmod +x docker/entrypoint.sh docker/flask-admin.sh \
 && ln -sf /app/docker/flask-admin.sh /usr/local/bin/flask-admin \
 && useradd -m otto && mkdir -p /data && chown -R otto /app /data
USER otto

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# No hard-coded -w: gunicorn honours the WEB_CONCURRENCY env var (default 1 if unset),
# so the worker count is tunable per environment without a rebuild.
# The compose file sets a sane default (2). Fewer workers = less RAM (each loads the app).
CMD ["gunicorn", "-b", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]

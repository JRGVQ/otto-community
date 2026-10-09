#!/bin/sh
# Web container runs migrations (and an idempotent demo seed) before booting.
# Worker/beat set RUN_MIGRATIONS=0 and skip straight to their command.
set -e

# ── Ensure stable secrets ────────────────────────────────────────────────────
# If SECRET_KEY / TOKEN_ENCRYPTION_KEY aren't supplied (or still hold a dev
# placeholder), generate them once and persist to the shared /data volume so all
# services (web/worker/beat) use the SAME keys across restarts.
SECRETS_DIR="${OTTO_DATA_DIR:-/data}/secrets"
if ! mkdir -p "$SECRETS_DIR" 2>/dev/null || [ ! -w "$SECRETS_DIR" ]; then
  SECRETS_DIR="$HOME/.otto-secrets"
  mkdir -p "$SECRETS_DIR" 2>/dev/null || SECRETS_DIR="/tmp/otto-secrets"
  mkdir -p "$SECRETS_DIR"
fi

case "${SECRET_KEY:-}" in
  "" | *insecure* | *change-me* | *change_me* | *CHANGE_ME* | *CHANGEME* | *changeme*)
    if [ ! -f "$SECRETS_DIR/secret_key" ]; then
      python -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRETS_DIR/secret_key"
      echo "[entrypoint] Generated a SECRET_KEY (persisted in $SECRETS_DIR)."
    fi
    SECRET_KEY="$(cat "$SECRETS_DIR/secret_key")"
    export SECRET_KEY
    ;;
esac

case "${TOKEN_ENCRYPTION_KEY:-}" in
  "" | *change-me* | *change_me* | *CHANGE_ME* | *CHANGEME* | *changeme*)
    if [ ! -f "$SECRETS_DIR/token_key" ]; then
      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$SECRETS_DIR/token_key"
      echo "[entrypoint] Generated a TOKEN_ENCRYPTION_KEY (persisted in $SECRETS_DIR)."
    fi
    TOKEN_ENCRYPTION_KEY="$(cat "$SECRETS_DIR/token_key")"
    export TOKEN_ENCRYPTION_KEY
    ;;
esac

if [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "[entrypoint] Running database migrations…"
  # `set -e` means a failed upgrade exits the container, which Docker then restarts —
  # a crash loop that presents to visitors as the proxy's "bad gateway" screen rather
  # than as a migration error. Say plainly what happened before that occurs.
  if ! flask db upgrade; then
    echo "[entrypoint] ✗ MIGRATIONS FAILED — the container will exit and restart."
    echo "[entrypoint]   The site stays down until this is resolved. Check the traceback"
    echo "[entrypoint]   above, then run: flask-admin db-doctor  (add --fix to repair drift)."
    exit 1
  fi
  # Report (never repair) any drift between the models and the live schema. When a restore
  # or a stray `db stamp` leaves alembic_version AHEAD of the real schema, `db upgrade`
  # succeeds as a no-op and the missing columns only surface later as 500s on live traffic.
  # Surfacing it in the deploy log turns that into something you see at deploy time.
  flask db-doctor || true
  if [ "$SEED_DEMO" = "1" ]; then
    echo "[entrypoint] Seeding demo organisation (idempotent)…"
    flask seed || true
  fi
fi

exec "$@"

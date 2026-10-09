#!/bin/sh
# flask-admin — run Flask CLI admin commands inside the Otto container
# (wipe-demo, create-owner, set-password, grant-owner, erase-user, …) without
# hitting "SECRET_KEY must be set to a secure value in production".
#
# A freshly opened terminal does NOT inherit the secrets the entrypoint generates
# at boot, so ProdConfig's fail-fast guard trips. This loads those persisted
# secrets (or whatever is already in the env, or a throwaway pair as a last
# resort) and then execs flask with your arguments. The keys only satisfy the
# startup guard — admin commands don't use them; data operations hit DATABASE_URL.
#
#   Usage:  flask-admin wipe-demo
#           flask-admin create-owner
#           flask-admin set-password you@example.com
set -eu

SECRETS_DIR="${OTTO_DATA_DIR:-/data}/secrets"

case "${SECRET_KEY:-}" in
  "" | *insecure* | *change-me* | *change_me* | *CHANGE_ME* | *CHANGEME* | *changeme*)
    if [ -f "$SECRETS_DIR/secret_key" ]; then
      SECRET_KEY="$(cat "$SECRETS_DIR/secret_key")"
    else
      SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    fi
    export SECRET_KEY
    ;;
esac

case "${TOKEN_ENCRYPTION_KEY:-}" in
  "" | *change-me* | *change_me* | *CHANGE_ME* | *CHANGEME* | *changeme*)
    if [ -f "$SECRETS_DIR/token_key" ]; then
      TOKEN_ENCRYPTION_KEY="$(cat "$SECRETS_DIR/token_key")"
    else
      TOKEN_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    fi
    export TOKEN_ENCRYPTION_KEY
    ;;
esac

exec flask "$@"

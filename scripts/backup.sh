#!/usr/bin/env bash
# Daily PostgreSQL backup for Otto. Run from the project directory (where docker-compose.yml lives).
# Keeps the last 14 daily dumps. Wire it to cron — see INSTALL.md.
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${OTTO_BACKUP_DIR:-/opt/otto-backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/otto-$STAMP.sql.gz"

docker compose exec -T db pg_dump -U otto otto | gzip > "$OUT"
echo "[backup] wrote $OUT"

# Retention: keep the 14 most recent.
ls -1t "$BACKUP_DIR"/otto-*.sql.gz | tail -n +15 | xargs -r rm -f
echo "[backup] retention applied (14 newest kept)"

#!/usr/bin/env bash
# Daily PostgreSQL backup for the docker compose deployment. Keeps the newest $KEEP_DAYS days.
#   crontab: 30 23 * * * /opt/rasta-bot/docker/backup_postgres.sh >> /opt/rasta-bot/backups/backup.log 2>&1
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
cd "$PROJECT_DIR"
set -a; . ./.env; set +a

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%d-%H%M%S)"
file="$BACKUP_DIR/${POSTGRES_DB}-${stamp}.sql.gz"
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner | gzip -9 > "$file.partial"
mv "$file.partial" "$file"
find "$BACKUP_DIR" -name "${POSTGRES_DB}-*.sql.gz" -mtime "+$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup written: $file"

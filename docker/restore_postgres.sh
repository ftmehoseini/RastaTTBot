#!/usr/bin/env bash
# Restore a backup made by backup_postgres.sh into the running postgres container.
#   docker/restore_postgres.sh backups/rasta_bot-20261020-233000.sql.gz
# Stop bot, worker and beat first:  docker compose stop bot worker beat
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: $0 <backup.sql.gz>"; exit 1; }
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
set -a; . ./.env; set +a
gunzip -c "$1" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
echo "restored $1"

#!/usr/bin/env bash
# Pull the latest code and restart the stack. Run on the server from anywhere:
#   /opt/rasta-bot/docker/deploy.sh            # deploys the current branch (normally main)
#   /opt/rasta-bot/docker/deploy.sh my-branch  # try a branch before it is merged
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes on the server. Commit/push them or run 'git stash' first:"
  git status --short --untracked-files=no
  exit 1
fi

echo "==> Backing up the database"
"$PROJECT_DIR/docker/backup_postgres.sh" || echo "warning: backup failed (first deploy?)"

echo "==> Updating to origin/$BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
git log -1 --oneline

echo "==> Rebuilding and restarting (migrations run automatically)"
docker compose up -d --build
docker image prune -f >/dev/null

sleep 5
docker compose ps
curl -fsS http://127.0.0.1:8080/health && echo && echo "==> Deployed $(git rev-parse --short HEAD)"

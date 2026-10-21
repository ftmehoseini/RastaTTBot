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

docker compose ps

# The bot needs a few seconds after "Started" before its health server answers.
echo "==> Waiting for the bot to report healthy"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/health; then
    echo
    echo "==> Deployed $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 2
done
echo "Bot did not become healthy in 60s. Check: docker compose logs --tail=50 bot"
exit 1

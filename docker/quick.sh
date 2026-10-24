#!/usr/bin/env bash
# Fast test loop while developing: pull the latest commit and restart the bot, no rebuild (~10s).
# Runs the containers with docker-compose.dev.yml, so they use the code straight from this folder.
# Use deploy.sh instead after changing requirements.txt, the Dockerfile or migrations, and for releases.
set -euo pipefail

# Inside main() so bash reads the whole file before git pull can change it.
main() {
  cd "$(dirname "$0")/.."
  git pull --ff-only
  git log -1 --oneline
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-deps --force-recreate bot worker beat
  echo "==> Restarted. Recent bot logs:"
  sleep 5
  docker compose logs --tail=15 bot
}

main "$@"

#!/usr/bin/env bash
# Copy this repo (with .git, so local branches and commits come along), .env
# and the Modal config to the server, then install everything there.
# Usage: scripts/server/push.sh <server-ip>
# Skips .venv, data/ (fetched on the server) and logs/ (too big; copy by hand).
set -euo pipefail
HOST="debate@$1"
cd "$(dirname "$0")/../.."

rsync -az \
  --exclude .venv --exclude data/ --exclude logs/ --exclude __pycache__ \
  --exclude 'shareable-evidence-bundles*' \
  ./ "$HOST:Debate-ASB/"
scp -q .env "$HOST:Debate-ASB/.env"
scp -q ~/.modal.toml "$HOST:.modal.toml"

ssh "$HOST" 'bash -lc "
  set -e
  cd Debate-ASB
  mkdir -p logs
  uv sync --extra pipelines
  scripts/fetch_asb.sh
  docker compose -f docker/compose.yaml build
  uv run modal profile activate debateasb
  uv run modal app list >/dev/null && echo modal ok
  df -h / | tail -1; free -h | head -2
"'

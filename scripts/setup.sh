#!/usr/bin/env bash
# Create the project environment, including pinned development tools.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync --locked

echo
echo "Environment ready. Optional next steps:"
echo "  scripts/fetch_asb.sh  # download ASB (~4.4 GB)"
echo "  echo 'OPENROUTER_API_KEY=sk-or-...' > .env"
echo "  uv run pytest"

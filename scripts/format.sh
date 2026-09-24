#!/usr/bin/env bash
# Apply the same import sorting and formatting used by the editor configuration.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff check --select I --fix .
uv run ruff format .

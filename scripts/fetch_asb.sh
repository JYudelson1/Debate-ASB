#!/usr/bin/env bash
# Fetch Auditing Sabotage Bench (codebases + rubrics) into data/asb. ~4.4 GB.
# Pinned to one commit so everyone runs on the same dataset.
set -euo pipefail
ASB_COMMIT=f8efd6834aa72ee91fe9042eaab852724873167a
cd "$(dirname "$0")/.."
if [ -d data/asb ]; then
  echo "data/asb already exists (at $(git -C data/asb rev-parse HEAD))"
  exit 0
fi
git init -q data/asb
git -C data/asb remote add origin https://github.com/ejcgan/auditing-sabotage-bench
git -C data/asb fetch --depth 1 origin "$ASB_COMMIT"
git -C data/asb checkout -q FETCH_HEAD

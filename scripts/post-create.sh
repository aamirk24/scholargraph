#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Keep large temporary package archives off the workspace filesystem.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

uv sync --frozen --no-cache

# The database service healthcheck normally completes before this script, but
# this loop also protects manual/devcontainer rebuilds.
for attempt in $(seq 1 30); do
  if pg_isready \
    -h db \
    -p 5432 \
    -U sguser \
    -d scholargraph \
    >/dev/null 2>&1; then
    break
  fi

  if [ "$attempt" -eq 30 ]; then
    echo "PostgreSQL did not become ready." >&2
    exit 1
  fi

  sleep 2
done

uv run alembic upgrade head

rm -rf "$UV_CACHE_DIR"

echo "ScholarGraph is ready. Run 'make dev' or 'make test'."

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

HEALTH_URL="http://127.0.0.1:8000/health"
FORWARD_URL="http://localhost:8000/docs"
MAX_ATTEMPTS=300

notify_when_ready() {
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    if curl --fail --silent --show-error "$HEALTH_URL" >/dev/null 2>&1; then
      echo
      echo "ScholarGraph is ready: $FORWARD_URL"
      echo
      return 0
    fi

    sleep 1
  done

  echo "ScholarGraph did not become ready within ${MAX_ATTEMPTS} seconds." >&2
  return 1
}

notify_when_ready &
notifier_pid=$!

cleanup() {
  kill "$notifier_pid" >/dev/null 2>&1 || true
  wait "$notifier_pid" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

uv run uvicorn \
  app.main:app \
  --reload \
  --host 0.0.0.0 \
  --port 8000

#!/usr/bin/env bash
# Run the FULL test suite locally against a real Postgres — including the
# DB-backed tests that ./scripts/verify.sh skips when AI_BOOKS_DB_URL is unset.
#
# Uses one lightweight `postgres:17-alpine` container (compose service `db`), NOT
# the full `supabase start` stack — the suite only needs Postgres. The container
# is left running between invocations for fast reuse (conftest isolates each test
# in a throwaway schema, so a single shared container is enough).
#
#   ./scripts/test.sh                 # full pytest suite against the container
#   ./scripts/test.sh -k journal -x   # extra args are forwarded to pytest
#   ./scripts/test.sh --web           # also run the web/ golden cross-check
#   ./scripts/test.sh --down          # stop & remove the test container, exit
#
# If AI_BOOKS_DB_URL is already set (e.g. in CI), it is honoured as-is and no
# container is started.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${AI_BOOKS_TEST_PORT:-54329}"
RUN_WEB=false
DOWN=false
PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --web) RUN_WEB=true ;;
    --down) DOWN=true ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

# docker compose v2 (preferred) or legacy docker-compose
if docker compose version > /dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose > /dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "error: docker compose is required (install Docker / docker compose)" >&2
  exit 2
fi

if [[ "$DOWN" == true ]]; then
  # No -v needed: the data dir is tmpfs (see compose.yaml), so removing the
  # container already discards all data.
  "${COMPOSE[@]}" down
  echo "✓ test database stopped"
  exit 0
fi

# Honour an externally-provided URL (CI); otherwise bring the container up.
if [[ -z "${AI_BOOKS_DB_URL:-}" ]]; then
  AI_BOOKS_TEST_PORT="$PORT" "${COMPOSE[@]}" up -d db
  printf 'waiting for postgres on :%s ' "$PORT"
  for _ in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T db pg_isready -U postgres -d ai_books_test > /dev/null 2>&1; then
      ready=1
      break
    fi
    printf '.'
    sleep 1
  done
  echo
  if [[ "${ready:-0}" != "1" ]]; then
    echo "error: postgres did not become ready in time" >&2
    exit 1
  fi
  export AI_BOOKS_DB_URL="postgresql://postgres:postgres@127.0.0.1:${PORT}/ai_books_test"
fi
echo "AI_BOOKS_DB_URL=$AI_BOOKS_DB_URL"

echo "=== pytest (full suite, DB-backed tests included) ==="
uv run pytest -q "${PYTEST_ARGS[@]}"

if [[ "$RUN_WEB" == true ]]; then
  echo "=== web golden cross-check (viewer numbers == report layer) ==="
  PYTHONPATH=. uv run python scripts/seed_verify_db.py
  (
    cd web
    [[ -d node_modules ]] || npm ci
    npm run verify:golden
  )
fi

echo "✓ done (container left running; './scripts/test.sh --down' to stop)"

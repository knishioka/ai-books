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
#   ./scripts/test.sh --pooler        # run the suite THROUGH a pgbouncer pooler (#52)
#   ./scripts/test.sh --down          # stop & remove the test containers, exit
#
# --pooler reproduces Supabase's production pooler (pgbouncer, transaction mode): it
# brings up the extra `pgbouncer` compose service in front of `db`, points
# AI_BOOKS_DB_URL at it, and runs the migrate + seed write path, the Python pooler
# safety tests (tests/test_pooler_db.py), and the web golden cross-check all over the
# pooler — proving the viewer's `prepare: false` path and the prepared-statement-free
# Python client survive transaction pooling. Implies the web golden check.
#
# If AI_BOOKS_DB_URL is already set (e.g. in CI), it is honoured as-is and no
# container is started.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${AI_BOOKS_TEST_PORT:-54329}"
POOLER_PORT="${AI_BOOKS_POOLER_PORT:-54330}"
RUN_WEB=false
DOWN=false
POOLER=false
PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --web) RUN_WEB=true ;;
    --pooler)
      POOLER=true
      RUN_WEB=true
      ;;
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
  # container already discards all data. `--profile pooler` so the pgbouncer
  # container (a profile service) is torn down too, not just `db`.
  "${COMPOSE[@]}" --profile pooler down
  echo "✓ test containers stopped"
  exit 0
fi

# Honour an externally-provided URL (CI); otherwise bring the container(s) up.
if [[ -z "${AI_BOOKS_DB_URL:-}" ]]; then
  if [[ "$POOLER" == true ]]; then
    AI_BOOKS_TEST_PORT="$PORT" AI_BOOKS_POOLER_PORT="$POOLER_PORT" \
      "${COMPOSE[@]}" --profile pooler up -d db pgbouncer
  else
    AI_BOOKS_TEST_PORT="$PORT" "${COMPOSE[@]}" up -d db
  fi

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

  if [[ "$POOLER" == true ]]; then
    printf 'waiting for pgbouncer on :%s ' "$POOLER_PORT"
    pooler_ready=0
    for _ in $(seq 1 60); do
      if "${COMPOSE[@]}" exec -T pgbouncer pg_isready -h 127.0.0.1 -p 5432 -U postgres \
        > /dev/null 2>&1; then
        pooler_ready=1
        break
      fi
      printf '.'
      sleep 1
    done
    echo
    if [[ "$pooler_ready" != "1" ]]; then
      echo "error: pgbouncer did not become ready in time" >&2
      exit 1
    fi
    # Route the entire flow (migrate / seed / reports / golden) through the pooler.
    export AI_BOOKS_POOLER_URL="postgresql://postgres:postgres@127.0.0.1:${POOLER_PORT}/ai_books_test"
    export AI_BOOKS_DB_URL="$AI_BOOKS_POOLER_URL"
  fi
fi
echo "AI_BOOKS_DB_URL=$AI_BOOKS_DB_URL"

if [[ "$POOLER" == true ]]; then
  # AI_BOOKS_POOLER_URL un-skips tests/test_pooler_db.py; its fixture migrates + seeds
  # FY2025 through the pooler, then exercises the read / write / aggregation / ledger /
  # e-Tax paths and the prepared-statement regression guard over pgbouncer.
  : "${AI_BOOKS_POOLER_URL:=$AI_BOOKS_DB_URL}"
  export AI_BOOKS_POOLER_URL
  echo "AI_BOOKS_POOLER_URL=$AI_BOOKS_POOLER_URL"
  echo "=== pytest (pooler safety suite, through pgbouncer) ==="
  uv run pytest -q tests/test_pooler_db.py "${PYTEST_ARGS[@]}"
else
  echo "=== pytest (full suite, DB-backed tests included) ==="
  # Measure coverage on the DB-backed full run and gate on the AGENTS.md targets
  # (line 80 / branch 70, #58). The gate runs only for an UNFILTERED full run — when
  # extra pytest args are forwarded (e.g. `-k journal`) coverage is partial, so we
  # measure but skip the gate to avoid false failures.
  COV_REPORTS=(--cov=src/ai_books --cov-branch --cov-report=term-missing
    --cov-report=xml:coverage.xml --cov-report=json:coverage.json)
  uv run pytest -q "${COV_REPORTS[@]}" "${PYTEST_ARGS[@]}"
  if [[ ${#PYTEST_ARGS[@]} -eq 0 ]]; then
    uv run python scripts/check_coverage.py coverage.json --line 80 --branch 70
  else
    echo "note: coverage gate skipped (pytest args filter the suite); measured only"
  fi
fi

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

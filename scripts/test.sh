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
#   ./scripts/test.sh --e2e           # Playwright browser smoke + fail-closed auth (#162)
#   ./scripts/test.sh --all           # ONE-COMMAND local guarantee — every block (#59)
#   ./scripts/test.sh --down          # stop & remove the test containers, exit
#
# --all is the single, mechanical "everything works locally" check (#59): it brings up
# Postgres + the pgbouncer pooler once, then runs EVERY guarantee block and prints a
# PASS/FAIL summary — the Python full suite (incl. MCP #50/#56, property #57, read-only
# role #54) with the coverage gate (#58), the pgbouncer pooler safety suite + viewer
# golden through the pooler (#52), the web unit layer + its coverage gate (#55/#58),
# the isolated Vercel-parity web build (#140), the e-Tax layout sync check, the viewer
# golden cross-check against a directly-connected Postgres, and the Playwright E2E smoke
# + fail-closed auth harness (#162, which boots its own `supabase start` stack). Unlike
# the other modes it does NOT stop at the first failure: every block runs so the summary
# shows the full picture, and the script exits non-zero if any block failed. This mirrors
# the CI jobs (verify / web / web-vercel-build / web-golden / pooler / web-e2e); see README
# "CI ↔ local guarantee mapping".
#
# --e2e boots the full local Supabase stack (`supabase start` — GoTrue auth + Postgres),
# seeds it, then runs `web/` Playwright specs against the production viewer build: all 10
# screens render, and unauthenticated / non-allowlisted access fails closed to /login. It
# exercises REAL Supabase Auth (no test-only bypass; AGENTS.md invariant #1 / ADR-0008).
# Requires the Supabase CLI. Extra args are forwarded to `playwright test`.
#
# --pooler reproduces Supabase's production pooler (pgbouncer, transaction mode): it
# brings up the extra `pgbouncer` compose service in front of `db`, points
# AI_BOOKS_DB_URL at it, and runs the migrate + seed write path, the Python pooler
# safety tests (tests/test_pooler_db.py), and the web golden cross-check all over the
# pooler — proving the viewer's `prepare: false` path and the prepared-statement-free
# Python client survive transaction pooling. Implies the web golden check.
#
# If AI_BOOKS_DB_URL is already set (e.g. in CI), it is honoured as-is and no
# container is started. --all additionally needs AI_BOOKS_POOLER_URL in that case.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${AI_BOOKS_TEST_PORT:-54329}"
POOLER_PORT="${AI_BOOKS_POOLER_PORT:-54330}"
RUN_WEB=false
DOWN=false
POOLER=false
ALL=false
E2E=false
PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --web) RUN_WEB=true ;;
    --pooler)
      POOLER=true
      RUN_WEB=true
      ;;
    --all) ALL=true ;;
    --e2e) E2E=true ;;
    --down) DOWN=true ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

if [[ "$ALL" == true && ${#PYTEST_ARGS[@]} -ne 0 ]]; then
  echo "error: --all runs the canonical full guarantee and takes no extra pytest args" >&2
  echo "       (got: ${PYTEST_ARGS[*]}). Use plain './scripts/test.sh <args>' to filter." >&2
  exit 2
fi

# Coverage reports for the DB-backed Python full run; the gate (line 80 / branch 70, #58)
# runs only for an UNFILTERED full run — partial runs measure but skip the gate.
COV_REPORTS=(--cov=src/ai_books --cov-branch --cov-report=term-missing
  --cov-report=xml:coverage.xml --cov-report=json:coverage.json)

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

# Ensure web/ deps are installed once before any block that needs them.
ensure_web_deps() {
  (cd web && { [[ -d node_modules ]] || npm ci; })
}

# E2E smoke harness (issue #162): boot the full local Supabase stack (GoTrue auth +
# Postgres) and run Playwright against the production viewer. Unlike every other mode this
# uses `supabase start` — NOT the lightweight `db` compose container — because the auth gate
# must be exercised against REAL Supabase Auth: there is no test-only bypass (AGENTS.md
# invariant #1 / ADR-0008 fail-closed). Shared by the `--e2e` mode and the `--all` summary.
run_e2e() {
  command -v supabase > /dev/null 2>&1 || {
    echo "error: the Supabase CLI is required for E2E (https://supabase.com/docs/guides/cli)" >&2
    return 1
  }
  echo "▶ supabase start (local GoTrue auth + Postgres)"
  supabase start || return 1
  # Export the stack's URL + keys for both `next build/start` and the Playwright setup
  # project. `supabase status -o env` emits API_URL / ANON_KEY / SERVICE_ROLE_KEY / DB_URL;
  # eval is safe here (trusted first-party CLI output).
  local _status
  _status="$(supabase status -o env)" || return 1
  eval "$_status"
  export NEXT_PUBLIC_SUPABASE_URL="$API_URL"
  export NEXT_PUBLIC_SUPABASE_ANON_KEY="$ANON_KEY"
  export SUPABASE_SERVICE_ROLE_KEY="$SERVICE_ROLE_KEY"
  export AI_BOOKS_DB_URL="$DB_URL"
  # The single-user allowlist the gate enforces; the Playwright setup provisions this owner
  # plus a non-owner outsider that must be denied.
  export AUTH_ALLOWED_EMAIL="${AUTH_ALLOWED_EMAIL:-owner-e2e@ai-books.test}"
  echo "NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL"
  echo "AI_BOOKS_DB_URL=$AI_BOOKS_DB_URL"
  # `supabase start` already applied supabase/migrations to its Postgres, so SEED ONLY (our
  # migrate.run() tracks in a separate schema_migrations table and would re-apply → a
  # CREATE TABLE conflict).
  PYTHONPATH=. uv run python scripts/seed_verify_db.py --seed-only || return 1
  ensure_web_deps || return 1
  # Install the Chromium build Playwright drives (with OS deps in CI, where apt is present).
  if [[ -n "${CI:-}" ]]; then
    (cd web && npx playwright install --with-deps chromium) || return 1
  else
    (cd web && npx playwright install chromium) || return 1
  fi
  (cd web && npx playwright test "$@")
}

if [[ "$E2E" == true ]]; then
  # Standalone E2E: no compose container — the Supabase stack owns Postgres here.
  run_e2e "${PYTEST_ARGS[@]}"
  exit $?
fi

# --all needs the pooler container too, so it shares --pooler's bring-up path.
NEED_POOLER=false
if [[ "$POOLER" == true || "$ALL" == true ]]; then
  NEED_POOLER=true
fi

# Honour an externally-provided URL (CI); otherwise bring the container(s) up.
if [[ -z "${AI_BOOKS_DB_URL:-}" ]]; then
  if [[ "$NEED_POOLER" == true ]]; then
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

  if [[ "$NEED_POOLER" == true ]]; then
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
    export AI_BOOKS_POOLER_URL="postgresql://postgres:postgres@127.0.0.1:${POOLER_PORT}/ai_books_test"
    # --pooler routes EVERYTHING through the pooler; --all keeps AI_BOOKS_DB_URL on the
    # direct port and only the pooler block flips to AI_BOOKS_POOLER_URL (so the direct
    # full suite and direct golden still exercise the non-pooled path).
    if [[ "$POOLER" == true ]]; then
      export AI_BOOKS_DB_URL="$AI_BOOKS_POOLER_URL"
    fi
  fi
fi
echo "AI_BOOKS_DB_URL=$AI_BOOKS_DB_URL"

# When the caller provided AI_BOOKS_DB_URL externally (no container brought up), --all
# still needs a pooler URL to run its pooler block. CI doesn't use --all (it splits the
# jobs), so this only guards an unusual manual invocation.
if [[ "$ALL" == true && -z "${AI_BOOKS_POOLER_URL:-}" ]]; then
  echo "error: --all with an externally-set AI_BOOKS_DB_URL also needs AI_BOOKS_POOLER_URL" >&2
  echo "       (a pgbouncer transaction-pooler URL in front of the same database)." >&2
  exit 2
fi

if [[ "$ALL" == true ]]; then
  echo "AI_BOOKS_POOLER_URL=$AI_BOOKS_POOLER_URL"
  echo
  echo "════════════════════════════════════════════════════════════════════"
  echo " ./scripts/test.sh --all — one-command local guarantee (#59)"
  echo "════════════════════════════════════════════════════════════════════"

  # Run every guarantee block, recording PASS/FAIL without aborting on the first
  # failure, then surface a summary and a single overall exit code. Each block is a
  # function whose commands are &&-chained, so it short-circuits internally and its
  # return status is reliable regardless of `set -e`'s in-function quirks.
  SUMMARY=()
  OVERALL=0
  run_block() {
    local label="$1"
    shift
    echo
    echo "────────────────────────────────────────────────────────────────────"
    echo "▶ ${label}"
    echo "────────────────────────────────────────────────────────────────────"
    if "$@"; then
      SUMMARY+=("PASS  ${label}")
    else
      SUMMARY+=("FAIL  ${label}")
      OVERALL=1
    fi
  }

  block_python() {
    # Full DB-backed suite (MCP #50/#56, property #57, read-only role #54, …) + gate.
    uv run pytest -q "${COV_REPORTS[@]}" &&
      uv run python scripts/check_coverage.py coverage.json --line 80 --branch 70
  }

  block_web_unit() {
    # Fast DB-free web unit layer (#55) under v8 coverage, gated to the #58 targets.
    ensure_web_deps &&
      (cd web && npm run test:coverage)
  }

  block_web_vercel_build() {
    # Vercel Root Directory=web parity (#140): build an isolated web/ copy so
    # build-time reads of ../src or other repo-root files fail before deployment.
    bash scripts/verify_web_vercel_build.sh
  }

  block_etax_layout_sync() {
    # The Python e-Tax layout JSONs are the SSOT; the web copies must be generated
    # byte-for-byte copies so Vercel's isolated root cannot drift silently.
    uv run python scripts/etax/sync_web_layouts.py --check
  }

  block_web_golden() {
    # Seed FY2025 through the production write path on the DIRECT connection, then
    # assert the viewer's numbers reproduce the report-layer golden byte-for-byte.
    PYTHONPATH=. uv run python scripts/seed_verify_db.py &&
      ensure_web_deps &&
      (cd web && npm run verify:golden)
  }

  block_pooler() {
    # Route the Python pooler safety suite over the original FY2025 golden fixture.
    # --all runs the direct viewer golden first, which seeds KOA220/KOA240 public
    # samples into the shared DB. Remove only those fixed synthetic sample entries
    # before the pooler safety tests so the FY2025 golden is not polluted.
    (
      export AI_BOOKS_DB_URL="$AI_BOOKS_POOLER_URL"
      uv run python - << 'PY' &&
from ai_books import db

with db.connect() as conn:
    with conn.transaction():
        conn.execute(
            """
            DELETE FROM journal_entries
            WHERE voucher_no LIKE 'RE2025-%'
               OR voucher_no LIKE 'AG2025-%'
            """
        )
        conn.execute(
            """
            DELETE FROM fiscal_years
            WHERE name IN ('FY2023-KOA220', 'FY2024-KOA240')
            """
        )
PY
        PYTHONPATH=. uv run python scripts/seed_verify_db.py --fy2025-only &&
        uv run pytest -q tests/test_pooler_db.py &&
        PYTHONPATH=. uv run python scripts/seed_verify_db.py &&
        { cd web && npm run verify:golden; }
    )
  }

  block_e2e() {
    # Browser smoke + fail-closed auth gate (#162). Runs in a subshell so its Supabase
    # env exports (AI_BOOKS_DB_URL → the local stack's Postgres) never leak back to the
    # direct-DB blocks above. Boots `supabase start` for REAL GoTrue auth (no bypass).
    (run_e2e)
  }

  run_block "Python full suite + coverage gate (direct DB)" block_python
  run_block "Web unit layer + coverage gate (vitest)" block_web_unit
  run_block "Web Vercel parity build (isolated web root)" block_web_vercel_build
  run_block "e-Tax layout sync check (Python SSOT -> web)" block_etax_layout_sync
  run_block "Viewer golden cross-check (direct DB)" block_web_golden
  run_block "Pooler safety suite + golden (through pgbouncer)" block_pooler
  run_block "Viewer E2E smoke + fail-closed auth (Playwright)" block_e2e

  echo
  echo "════════════════════════════════════════════════════════════════════"
  echo " SUMMARY"
  echo "════════════════════════════════════════════════════════════════════"
  for line in "${SUMMARY[@]}"; do
    if [[ "$line" == PASS* ]]; then
      printf '  ✓ %s\n' "${line#PASS  }"
    else
      printf '  ✗ %s\n' "${line#FAIL  }"
    fi
  done
  echo "════════════════════════════════════════════════════════════════════"
  if [[ "$OVERALL" -ne 0 ]]; then
    echo "✗ one or more blocks FAILED (container left running; './scripts/test.sh --down' to stop)"
    exit 1
  fi
  echo "✓ all guarantee blocks PASSED (container left running; './scripts/test.sh --down' to stop)"
  exit 0
fi

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

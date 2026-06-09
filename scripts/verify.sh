#!/usr/bin/env bash
# verify.sh — ai-books verification entrypoint.
# See AGENTS.md "Verification" for the contract this script implements.
#
# Usage:
#   ./scripts/verify.sh           # human-readable (text)
#   ./scripts/verify.sh --json    # structured output (CI / Codex)
#   ./scripts/verify.sh --web     # also run web lint/typecheck/unit tests
#
# Exit codes: 0 all pass / n/a, 1 one or more fail, 2 environment error.

set -euo pipefail

# ai-books is a Python library; no separate build step.
BUILD_CMD=""
LINT_CMD="uv run ruff check ."
FORMAT_CMD="uv run ruff format --check ."
TYPECHECK_CMD="uv run mypy src tests"
ETAX_LAYOUT_CMD="uv run python scripts/etax/sync_web_layouts.py --check"

# Coverage (#58). Always *measure* (term-missing for humans; xml + json as CI artifacts),
# but only *gate* on the AGENTS.md targets (line 80 / branch 70) when a live DB is configured.
# A DB-less verify.sh skips the DB-backed tests and so under-reports coverage — gating there
# would produce false failures, so we measure-only. CI sets AI_BOOKS_DB_URL (Postgres service),
# so the gate is enforced on every PR; `scripts/test.sh` likewise runs with a DB.
COV_REPORTS="--cov=src/ai_books --cov-branch --cov-report=term-missing"
COV_REPORTS+=" --cov-report=xml:coverage.xml --cov-report=json:coverage.json"
if [[ -n "${AI_BOOKS_DB_URL:-}" ]]; then
  TEST_CMD="uv run pytest -q $COV_REPORTS"
  TEST_CMD+=" && uv run python scripts/check_coverage.py coverage.json --line 80 --branch 70"
else
  TEST_CMD="uv run pytest -q $COV_REPORTS"
fi

JSON_MODE=0
RUN_WEB=0
for arg in "$@"; do
  case "$arg" in
    --json) JSON_MODE=1 ;;
    --web) RUN_WEB=1 ;;
    *)
      echo "error: unknown argument: $arg" >&2
      echo "usage: ./scripts/verify.sh [--json] [--web]" >&2
      exit 2
      ;;
  esac
done
WEB_CMD=""
if [[ $RUN_WEB -eq 1 ]]; then
  WEB_CMD="cd web && { [[ -d node_modules ]] || npm ci; } && npm run lint && npm run typecheck && npm run test:coverage"
fi

RESULT_BUILD=""
RESULT_LINT=""
RESULT_FORMAT=""
RESULT_TYPECHECK=""
RESULT_ETAX_LAYOUT=""
RESULT_TEST=""
RESULT_WEB=""
FAILURES_JSON=""
ANY_FAIL=0

log_text() {
  if [[ $JSON_MODE -eq 0 ]]; then
    printf '%s\n' "$1"
  fi
}

run_step() {
  local name="$1" result_var="$2" cmd="$3"
  if [[ -z "$cmd" ]]; then
    printf -v "$result_var" '%s' "n/a"
    log_text "$(printf '  %-10s n/a' "$name")"
    return 0
  fi
  log_text "$(printf '  %-10s running: %s' "$name" "$cmd")"
  local exit_code=0
  bash -c "$cmd" > /tmp/verify-"$name".log 2>&1 || exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    printf -v "$result_var" '%s' "pass"
    log_text "$(printf '  %-10s ✅ pass' "$name")"
  else
    printf -v "$result_var" '%s' "fail"
    ANY_FAIL=1
    local sep=""
    [[ -n "$FAILURES_JSON" ]] && sep=","
    FAILURES_JSON+="${sep}{\"step\":\"$name\",\"exit\":$exit_code}"
    log_text "$(printf '  %-10s ❌ fail (exit=%d) — see /tmp/verify-%s.log' "$name" "$exit_code" "$name")"
  fi
}

log_text "verify.sh: starting (ai-books)"

run_step build RESULT_BUILD "$BUILD_CMD"
run_step lint RESULT_LINT "$LINT_CMD"
run_step format RESULT_FORMAT "$FORMAT_CMD"
run_step typecheck RESULT_TYPECHECK "$TYPECHECK_CMD"
run_step etax_layout RESULT_ETAX_LAYOUT "$ETAX_LAYOUT_CMD"
run_step test RESULT_TEST "$TEST_CMD"
run_step web RESULT_WEB "$WEB_CMD"

if [[ $JSON_MODE -eq 1 ]]; then
  printf '{"build":"%s","lint":"%s","format":"%s","typecheck":"%s","etax_layout":"%s","test":"%s","web":"%s","failures":[%s]}\n' \
    "$RESULT_BUILD" "$RESULT_LINT" "$RESULT_FORMAT" "$RESULT_TYPECHECK" "$RESULT_ETAX_LAYOUT" \
    "$RESULT_TEST" "$RESULT_WEB" "$FAILURES_JSON"
fi

if [[ $ANY_FAIL -eq 1 ]]; then
  exit 1
fi
exit 0

#!/usr/bin/env bash
# verify.sh — ai-books verification entrypoint.
# See AGENTS.md "Verification" for the contract this script implements.
#
# Usage:
#   ./scripts/verify.sh           # human-readable (text)
#   ./scripts/verify.sh --json    # structured output (CI / Codex)
#
# Exit codes: 0 all pass / n/a, 1 one or more fail, 2 environment error.

set -euo pipefail

# ai-books is a Python library; no separate build step.
BUILD_CMD=""
LINT_CMD="uv run ruff check ."
FORMAT_CMD="uv run ruff format --check ."
TYPECHECK_CMD="uv run mypy src tests"
TEST_CMD="uv run pytest -q"

JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

RESULT_BUILD=""
RESULT_LINT=""
RESULT_FORMAT=""
RESULT_TYPECHECK=""
RESULT_TEST=""
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
  bash -c "$cmd" >/tmp/verify-"$name".log 2>&1 || exit_code=$?
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

run_step build     RESULT_BUILD     "$BUILD_CMD"
run_step lint      RESULT_LINT      "$LINT_CMD"
run_step format    RESULT_FORMAT    "$FORMAT_CMD"
run_step typecheck RESULT_TYPECHECK "$TYPECHECK_CMD"
run_step test      RESULT_TEST      "$TEST_CMD"

if [[ $JSON_MODE -eq 1 ]]; then
  printf '{"build":"%s","lint":"%s","format":"%s","typecheck":"%s","test":"%s","failures":[%s]}\n' \
    "$RESULT_BUILD" "$RESULT_LINT" "$RESULT_FORMAT" "$RESULT_TYPECHECK" "$RESULT_TEST" \
    "$FAILURES_JSON"
fi

if [[ $ANY_FAIL -eq 1 ]]; then
  exit 1
fi
exit 0

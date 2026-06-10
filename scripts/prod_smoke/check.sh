#!/usr/bin/env bash
# Production smoke for the read-only viewer (#167).
#
# Asserts "本番が実際に動いていて、かつ未認証では何も漏れない" against the deployed
# Vercel viewer. Two modes, selected by PROD_SMOKE_MODE (repo variable):
#
#   public — the deployment intentionally runs the public sample mode
#            (AI_BOOKS_VIEWER_PUBLIC=true, synthetic data only): every report page
#            must render (200 + its h1 marker + the read-only footer), /login must
#            render, and no page may 5xx.
#   gated  — the deployment runs the single-user auth gate (#108 / ADR-0008):
#            every report page must redirect to /login WITHOUT leaking its report
#            marker in the body; /login must render.
#
# The default is **gated** (fail-closed, matching the repo philosophy): if the
# mode variable is lost, the check fails towards "公開のつもりがないのに公開",
# never the other way.
#
# Log safety: only HTTP statuses and marker presence are printed — response
# bodies are NEVER echoed (a gated deployment serves real 帳簿 figures).
set -euo pipefail

BASE_URL="${BASE_URL:-}"
MODE="${MODE:-gated}"
SUMMARY="${SUMMARY:-}" # optional markdown failure summary (consumed by the workflow issue step)
SIMULATE_FAILURE="${SIMULATE_FAILURE:-}"
CURL_OPTS=(--silent --show-error --max-time 30 --retry 2 --retry-delay 3)

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: BASE_URL is empty — set the PROD_SMOKE_BASE_URL repo secret ([admin], issue #167)." >&2
  exit 2
fi
case "$MODE" in
  public | gated) ;;
  *)
    echo "ERROR: MODE must be 'public' or 'gated', got '${MODE}'." >&2
    exit 2
    ;;
esac
BASE_URL="${BASE_URL%/}"

# Route → h1 marker (web/components/report-header.tsx renders <h1>{title}</h1>;
# the home page renders its own <h1>). Keep in sync with web/app/*/page.tsx.
ROUTES=(
  "/|ai-books viewer"
  "/trial-balance|合計残高試算表"
  "/monthly-trend|月次推移"
  "/journal|仕訳帳"
  "/ledger|総勘定元帳"
  "/pl|損益計算書"
  "/bs|貸借対照表"
  "/worksheet|精算表"
  "/statements|青色申告決算書"
  "/etax|e-Tax 取込データ"
)
READONLY_FOOTER="read-only viewer"
LOGIN_PATH="/login"

FAILURES=()

fail() {
  local message="$1"
  FAILURES+=("$message")
  echo "FAIL: $message"
}

# fetch <url> <body_file> — status code on stdout, redirects NOT followed.
fetch() {
  local url="$1" body_file="$2"
  curl "${CURL_OPTS[@]}" -o "$body_file" -w '%{http_code}' "$url" || echo "000"
}

redirect_location() {
  local url="$1"
  curl "${CURL_OPTS[@]}" -o /dev/null -w '%{redirect_url}' "$url" || true
}

body_tmp=$(mktemp)
trap 'rm -f "$body_tmp"' EXIT

echo "prod-smoke: mode=${MODE} base=${BASE_URL}"

# /login renders in both modes.
status=$(fetch "${BASE_URL}${LOGIN_PATH}" "$body_tmp")
echo "check ${LOGIN_PATH}: status=${status} (expect 200)"
[[ "$status" == "200" ]] || fail "${LOGIN_PATH} returned ${status} (expected 200)"

for entry in "${ROUTES[@]}"; do
  route="${entry%%|*}"
  marker="${entry##*|}"
  status=$(fetch "${BASE_URL}${route}" "$body_tmp")

  if [[ "$MODE" == "public" ]]; then
    marker_found="no"
    footer_found="no"
    grep -qF "$marker" "$body_tmp" && marker_found="yes"
    grep -qF "$READONLY_FOOTER" "$body_tmp" && footer_found="yes"
    echo "check ${route}: status=${status} marker=${marker_found} readonly_footer=${footer_found} (expect 200/yes/yes)"
    [[ "$status" == "200" ]] || fail "${route} returned ${status} (expected 200 in public mode)"
    [[ "$marker_found" == "yes" ]] || fail "${route} did not render its heading marker"
    [[ "$footer_found" == "yes" ]] || fail "${route} lost the read-only footer (書込 UI 混入の兆候)"
  else
    # gated: must redirect to /login and must NOT leak the report marker. The home
    # page's marker is the app shell title, which the login screen legitimately
    # shares — leak detection uses the report pages' unique h1 titles.
    location=$(redirect_location "${BASE_URL}${route}")
    leaked="no"
    if [[ "$route" != "/" ]] && grep -qF "$marker" "$body_tmp"; then
      leaked="yes"
    fi
    echo "check ${route}: status=${status} location=${location:-none} leak=${leaked} (expect 3xx→${LOGIN_PATH}/no)"
    case "$status" in
      30[1278]) ;;
      *) fail "${route} returned ${status} (expected a redirect in gated mode)" ;;
    esac
    [[ "$location" == *"${LOGIN_PATH}"* ]] || fail "${route} redirect did not target ${LOGIN_PATH}"
    [[ "$leaked" == "no" ]] || fail "${route} body leaked report content while unauthenticated"
  fi
done

if [[ -n "$SIMULATE_FAILURE" ]]; then
  fail "simulated failure (workflow_dispatch simulate_failure=true) — issue-creation path drill"
fi

if ((${#FAILURES[@]} > 0)); then
  echo "prod-smoke: ${#FAILURES[@]} failure(s)"
  if [[ -n "$SUMMARY" ]]; then
    {
      echo "本番スモーク (#167) が失敗しました。mode=\`${MODE}\`"
      echo
      for f in "${FAILURES[@]}"; do
        echo "- ${f}"
      done
      echo
      echo "確認手順は docs/ops/prod-smoke.md を参照。"
    } > "$SUMMARY"
  fi
  exit 1
fi

echo "prod-smoke: all checks passed"

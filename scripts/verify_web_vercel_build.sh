#!/usr/bin/env bash
# Verify the web viewer under the same filesystem boundary as Vercel's
# Root Directory=web deployment: the build can see web/ and dependencies only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"

cleanup() {
  if [[ -n "${TMP_ROOT:-}" && -d "$TMP_ROOT" ]]; then
    rm -rf "$TMP_ROOT"
  fi
}
trap cleanup EXIT

WEB_ROOT="$TMP_ROOT/web"
mkdir -p "$WEB_ROOT"

if ! command -v rsync > /dev/null 2>&1; then
  echo "error: rsync is required but not installed. Please install rsync or ensure it is available in PATH." >&2
  exit 1
fi

rsync -a --delete \
  --exclude node_modules \
  --exclude .next \
  --exclude coverage \
  "$REPO_ROOT/web/" "$WEB_ROOT/"

echo "=== Vercel parity web build (isolated web/ root) ==="
echo "source: $REPO_ROOT/web"
echo "build:  $WEB_ROOT"

(
  cd "$WEB_ROOT"
  npm ci
  npm run build
)

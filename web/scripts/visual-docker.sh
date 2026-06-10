#!/usr/bin/env bash
#
# Visual-regression runner (issue #165) — runs the Playwright `visual` project INSIDE the pinned
# `mcr.microsoft.com/playwright` container so the rendering environment is byte-identical between
# baseline creation and every later comparison, on any host OS. macOS font rendering differs from
# Linux, so the `visual` project is gated behind PLAYWRIGHT_VISUAL=1 (set here, never on a bare
# host run) and the committed baselines are Linux-only — see playwright.config.ts / web/README.md.
#
# Local: `npm run e2e:visual` (compare) / `npm run e2e:visual:update` (create or refresh baselines).
# CI: the `web-e2e` job calls this script after the host-native smoke step.
#
# Prerequisite on the HOST: a running local Supabase stack (`supabase start`). This script derives
# its URL/keys, seeds the synthetic FY fixture, then drives Playwright in the container; the viewer
# is built + served inside the container (`reuseExistingServer:false` under CI=1) and reaches the
# host stack via `host.docker.internal`. Extra args are forwarded to `playwright test`.
set -euo pipefail

# Run from the repo root regardless of where npm invoked us (npm sets cwd to web/).
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Keep this tag in lockstep with web/package.json's @playwright/test version — a mismatched
# container would compare against a different Chromium build and drift the baselines.
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.60.0-noble"
E2E_PORT="${E2E_PORT:-3100}"

command -v supabase > /dev/null 2>&1 || {
  echo "error: the Supabase CLI is required (https://supabase.com/docs/guides/cli)" >&2
  exit 1
}
command -v uv > /dev/null 2>&1 || {
  echo "error: uv is required to seed the database (https://github.com/astral-sh/uv)" >&2
  exit 1
}
supabase status > /dev/null 2>&1 || {
  echo "error: no local Supabase stack — run 'supabase start' first" >&2
  exit 1
}

# `supabase status -o env` emits API_URL / ANON_KEY / SERVICE_ROLE_KEY / DB_URL bound to
# 127.0.0.1; eval is safe (trusted first-party CLI).
eval "$(supabase status -o env)"

# Seed on the HOST against the loopback URL (the host cannot resolve host.docker.internal).
# `supabase start` already applied supabase/migrations, so SEED ONLY (idempotent) — same call the
# `run_e2e` smoke path uses, so visual and smoke read identical synthetic data.
AI_BOOKS_DB_URL="$DB_URL" PYTHONPATH=. uv run python scripts/seed_verify_db.py --seed-only

# Rewrite the loopback host to host.docker.internal for the CONTAINER (and the Chromium it drives),
# so they reach the host's stack — resolved on both Docker Desktop (macOS) and Linux via --add-host.
# Cover both loopback spellings the CLI/env might use (127.0.0.1 and localhost).
to_host_internal() {
  local url="${1//127.0.0.1/host.docker.internal}"
  printf '%s' "${url//localhost/host.docker.internal}"
}
export NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_URL="$(to_host_internal "$API_URL")"
export NEXT_PUBLIC_SUPABASE_ANON_KEY="$ANON_KEY"
export SUPABASE_SERVICE_ROLE_KEY="$SERVICE_ROLE_KEY"
export AI_BOOKS_DB_URL
AI_BOOKS_DB_URL="$(to_host_internal "$DB_URL")"
# The single-user allowlist the gate enforces; the setup project provisions this owner.
export AUTH_ALLOWED_EMAIL="${AUTH_ALLOWED_EMAIL:-owner-e2e@ai-books.test}"

# Named volumes (NOT anonymous) so the Linux node_modules + Next build cache PERSIST across runs —
# `docker run --rm` discards anonymous volumes, forcing a full reinstall + cold build every time.
# They also mask the host-mounted darwin binaries, so the container's tree never clobbers the host's.
# Reinstall strategy: a reproducible `npm ci` in CI (ephemeral runners, lockfile-strict, never
# rewrites the mounted lockfile); a cache-reusing `npm install` locally, because `npm ci` wipes
# node_modules first and would defeat the persistent volume. With the lockfile in sync `npm install`
# is a near-no-op that leaves it untouched.
INSTALL="npm install --no-audit --no-fund"
[[ -n "${CI:-}" ]] && INSTALL="npm ci"

# --ipc=host: Chromium needs more shared memory than a container's default 64MB /dev/shm — without
#   this it crashes mid-render ("Page crashed"), the Playwright-recommended fix for Docker.
docker run --rm \
  --ipc=host \
  --add-host=host.docker.internal:host-gateway \
  -v "$REPO_ROOT":/work -w /work/web \
  -v ai-books-web-node_modules:/work/web/node_modules \
  -v ai-books-web-next:/work/web/.next \
  -e CI=1 \
  -e PLAYWRIGHT_VISUAL=1 \
  -e E2E_PORT="$E2E_PORT" \
  -e NEXT_PUBLIC_SUPABASE_URL \
  -e NEXT_PUBLIC_SUPABASE_ANON_KEY \
  -e SUPABASE_SERVICE_ROLE_KEY \
  -e AI_BOOKS_DB_URL \
  -e AUTH_ALLOWED_EMAIL \
  "$PLAYWRIGHT_IMAGE" \
  bash -c "$INSTALL && npx playwright test --project=visual ${*:-}"

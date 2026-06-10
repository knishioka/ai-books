import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E smoke harness for the read-only viewer (issue #162).
 *
 * The viewer's *numbers* are already proven byte-for-byte by the golden cross-check
 * (`npm run verify:golden`); this harness proves the orthogonal thing goldens cannot —
 * that every screen actually renders in a real browser and that the **auth gate is fail
 * closed** (an unauthenticated visitor never sees data). It is a smoke layer, not a numbers
 * layer: it asserts headings and redirects, never figures.
 *
 * Auth is exercised against **real Supabase Auth (GoTrue)** — there is no test-only bypass
 * (AGENTS.md invariant #1 / ADR-0008 fail-closed). The local stack (`supabase start`) or the
 * CI stack (`supabase/setup-cli` + `supabase start`) provides GoTrue; the `auth.setup.ts`
 * project provisions the owner via the service-role admin API, signs in through the real
 * login form and saves the resulting cookie as `storageState` for the authenticated specs.
 *
 * Driven by `./scripts/test.sh --e2e`, which boots the stack and exports the env below.
 */

const PORT = Number(process.env.E2E_PORT ?? 3100);
const BASE_URL = `http://127.0.0.1:${PORT}`;

/** Persisted owner session, produced by the `setup` project and reused by the smoke specs. */
export const OWNER_STORAGE_STATE = "e2e/.auth/owner.json";

export default defineConfig({
  testDir: "./e2e",
  // Smoke specs are independent reads of the same data, so they parallelise freely.
  fullyParallel: true,
  // Never let a stray `test.only` pass CI silently.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    // Provisions the owner identity and writes the storageState the smoke specs depend on.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "smoke",
      testIgnore: /auth\.setup\.ts/,
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["setup"],
    },
  ],
  // Build + serve the production viewer wired to the local Supabase stack. The env vars are
  // exported by `./scripts/test.sh --e2e` (NEXT_PUBLIC_SUPABASE_* / AUTH_ALLOWED_EMAIL /
  // AI_BOOKS_DB_URL); `next build` inlines the NEXT_PUBLIC_* values, so they must be set here.
  webServer: {
    command: `npm run build && npm run start -- --port ${PORT}`,
    url: BASE_URL,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
});

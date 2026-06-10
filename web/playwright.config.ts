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

/**
 * Visual-regression is opt-in via `PLAYWRIGHT_VISUAL=1`. Pixel baselines are platform-bound, so
 * the `visual` project must run in the SAME environment that produced its baselines — the pinned
 * `mcr.microsoft.com/playwright` container, driven by `scripts/visual-docker.sh` (which sets this
 * flag). A bare host `npx playwright test` (e.g. macOS local, or the host-native `web-e2e` smoke
 * step) therefore never runs it, so host font rendering can never produce a phantom diff. See
 * web/e2e/visual.spec.ts and web/README.md → "Visual regression". (issue #165)
 */
const VISUAL = !!process.env.PLAYWRIGHT_VISUAL;

/** Persisted owner session, produced by the `setup` project and reused by the smoke/visual specs. */
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
  expect: {
    toHaveScreenshot: {
      // Baselines are generated in the same pinned container that compares them, so anti-alias
      // jitter is near-zero — keep the tolerance tight enough that a real layout shift (a shifted
      // border, a wrapped 段階利益 row) always trips it, but non-zero so harmless sub-pixel font
      // hinting never does. Decided empirically at baseline creation (issue #165).
      maxDiffPixelRatio: 0.002,
      // Hide the text caret; animations are disabled by Playwright's screenshot default.
      caret: "hide",
    },
  },
  projects: [
    // Provisions the owner identity and writes the storageState the smoke/visual specs depend on.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "smoke",
      // Smoke runs every authenticated spec EXCEPT setup and the opt-in visual baselines.
      testIgnore: /auth\.setup\.ts|visual\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["setup"],
    },
    // Pixel baselines for the 決算書 preview + print layout. Opt-in (container-only) — see VISUAL.
    ...(VISUAL
      ? [
          {
            name: "visual",
            testMatch: /visual\.spec\.ts/,
            // Fix the viewport width so full-page captures have a deterministic layout width.
            use: {
              ...devices["Desktop Chrome"],
              viewport: { width: 1280, height: 1024 },
            },
            dependencies: ["setup"],
          },
        ]
      : []),
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

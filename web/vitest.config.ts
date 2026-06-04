import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * Vitest config for the viewer's pure-logic unit layer.
 *
 * These tests exercise `lib/reports/*`, `lib/etax/*` and the money/format helpers with **no
 * database** — the slow, DB-backed golden cross-check (`verify:golden`, #17/#25) stays the
 * source of truth for end-to-end numbers, while this layer pins the符号則・境界・端数・検証 edge
 * cases so a regression is caught in milliseconds (#55). DB-bound modules (`lib/db.ts`,
 * `lib/reports/fiscal-year.ts`, `lib/reports/context.ts`) import `server-only` and are out of
 * scope here.
 */
export default defineConfig({
  resolve: {
    // Mirror tsconfig's `@/*` → repo-root path alias so tests can import either way.
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      // Report on the pure modules this layer covers; the DB-bound modules belong to the
      // golden cross-check, not the unit layer. (Threshold enforcement is #58.)
      include: [
        "lib/money.ts",
        "lib/format.ts",
        "lib/reports/**",
        "lib/etax/**",
      ],
      exclude: [
        "lib/reports/fiscal-year.ts",
        "lib/reports/context.ts",
        "lib/reports/sql.ts",
        "lib/**/*.test.ts",
      ],
    },
  },
});

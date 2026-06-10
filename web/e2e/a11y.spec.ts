import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Result } from "axe-core";

import { REPORT_ROUTES } from "@/lib/routes";

import { OWNER_STORAGE_STATE } from "../playwright.config";
import {
  filterAllowed,
  GATING_IMPACTS,
  loadAllowlist,
  parseAllowlist,
} from "./a11y-allowlist";

/**
 * Accessibility regression sweep (issue #166). Runs `@axe-core/playwright` against every screen the
 * smoke harness covers — the ten authenticated report routes plus the unauthenticated `/login` —
 * and **gates on zero serious/critical violations**. This is the mechanical guard for the
 * affordances added in 520021f: a future change that reintroduces a contrast/label/landmark
 * regression fails CI instead of shipping silently.
 *
 * Lower-impact findings (`moderate` / `minor`) are *report-only*: attached to the Playwright report
 * for visibility but never failing the build, so the gate stays meaningful (#166 scope — not full
 * WCAG conformance). Genuinely unavoidable serious/critical nodes go in `a11y-allowlist.json`, keyed
 * by `ruleId × selector` with a mandatory reason; everything else is fixed in place.
 */

// Standard WCAG 2.0/2.1 A + AA rule set — the conformance bar the gate enforces.
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] as const;

/** The ten authenticated report screens, kept in lockstep with the nav via REPORT_ROUTES. */
const AUTHED_PATHS = REPORT_ROUTES.map((route) => route.href);

/** Compact, assertion-friendly view of a violation (full detail lives in the report). */
function summarize(violations: Result[]) {
  return violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => n.target.flat(Infinity).join(" ")),
  }));
}

/**
 * Scan the current page, attach all violations to the report for visibility, and return only the
 * serious/critical ones that survive the allowlist — i.e. the set that fails the gate.
 */
async function scanForGatingViolations(page: import("@playwright/test").Page) {
  const allow = loadAllowlist();
  const results = await new AxeBuilder({ page })
    .withTags([...WCAG_TAGS])
    .analyze();

  // Report-only: every violation (incl. moderate/minor) is recorded for the reviewer.
  await test.info().attach("axe-violations.json", {
    body: JSON.stringify(summarize(results.violations), null, 2),
    contentType: "application/json",
  });

  return filterAllowed(results.violations, allow).filter((v) =>
    GATING_IMPACTS.has(v.impact ?? null),
  );
}

test.describe("accessibility — authenticated report screens", () => {
  test.use({ storageState: OWNER_STORAGE_STATE });

  for (const path of AUTHED_PATHS) {
    test(`no serious/critical axe violations on ${path}`, async ({ page }) => {
      const response = await page.goto(path);
      expect(response?.status(), `${path} should respond 200`).toBe(200);

      const gating = await scanForGatingViolations(page);
      expect(
        summarize(gating),
        `serious/critical axe violations on ${path}`,
      ).toEqual([]);
    });
  }
});

test.describe("accessibility — login screen", () => {
  // Fresh, unauthenticated context (no storageState): the gate must hold on the public surface too.
  test("no serious/critical axe violations on /login", async ({ page }) => {
    const response = await page.goto("/login");
    expect(response?.status(), "/login should respond 200").toBe(200);

    const gating = await scanForGatingViolations(page);
    expect(
      summarize(gating),
      "serious/critical axe violations on /login",
    ).toEqual([]);
  });
});

test.describe("a11y allowlist mechanism", () => {
  // Pure data assertions (no page) — proves the allowlist file is well-formed and, crucially, that
  // the reason-required invariant is enforced rather than merely documented (#166 acceptance).
  test("the on-disk allowlist is well-formed", () => {
    expect(() => loadAllowlist()).not.toThrow();
  });

  test("every entry requires a non-empty reason", () => {
    expect(() =>
      parseAllowlist([{ ruleId: "color-contrast", selector: ".x" }]),
    ).toThrow(/reason/);
    expect(() =>
      parseAllowlist([
        { ruleId: "color-contrast", selector: ".x", reason: "" },
      ]),
    ).toThrow(/reason/);
    expect(() =>
      parseAllowlist([
        { ruleId: "color-contrast", selector: ".x", reason: "   " },
      ]),
    ).toThrow(/reason/);
  });

  test("ruleId and selector are likewise mandatory", () => {
    expect(() => parseAllowlist([{ selector: ".x", reason: "y" }])).toThrow(
      /ruleId/,
    );
    expect(() =>
      parseAllowlist([{ ruleId: "color-contrast", reason: "y" }]),
    ).toThrow(/selector/);
  });

  test("a well-formed entry parses", () => {
    const parsed = parseAllowlist([
      {
        ruleId: "color-contrast",
        selector: ".x",
        reason: "third-party widget",
      },
    ]);
    expect(parsed).toEqual([
      {
        ruleId: "color-contrast",
        selector: ".x",
        reason: "third-party widget",
      },
    ]);
  });
});

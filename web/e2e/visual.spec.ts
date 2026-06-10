import { expect, type Page, test } from "@playwright/test";

import { OWNER_STORAGE_STATE } from "../playwright.config";

/**
 * Visual-regression baselines for the 決算書 preview + print layout (issue #165).
 *
 * The golden cross-check proves the *numbers* and the smoke specs prove a screen *renders*;
 * this layer proves the orthogonal thing neither can — that the **layout itself** (and the
 * `@media print` rules in `globals.css`) does not silently break when CSS changes. A pixel diff
 * is the only mechanical way to catch "崩れた決算書プレビュー" before it reaches a printed 提出資料.
 *
 * Pixel comparison is platform-bound, so this project runs in ONE pinned environment — the
 * `mcr.microsoft.com/playwright` container — both locally (`npm run e2e:visual`) and in CI's
 * `web-e2e` job. The `visual` project is therefore gated behind `PLAYWRIGHT_VISUAL=1` (set only
 * by `scripts/visual-docker.sh`); a bare host `npx playwright test` never runs it, so macOS font
 * rendering can never produce a phantom diff (see playwright.config.ts + web/README.md).
 *
 * Only the synthetic `seed_fy` FY2025 fixture is ever captured — never real 確定数値.
 */
test.use({ storageState: OWNER_STORAGE_STATE });

/** Navigate, prove the screen actually rendered (its `<h1>`), and wait for web-fonts to settle
 *  so a screenshot is never taken mid-font-swap (a common source of flaky CJK diffs). */
async function gotoReady(
  page: Page,
  path: string,
  heading: string,
): Promise<void> {
  await page.goto(path);
  await expect(
    page.getByRole("heading", { level: 1, name: heading }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

/**
 * Screen (on-display) baselines. `/statements` is the primary target (the whole point of the
 * issue); the 試算表 and 損益計算書 are added as representative report layouts so a global CSS
 * regression (table borders, spacing, the 段階利益 emphasis) is caught beyond the 決算書 face.
 */
const SCREEN_TARGETS = [
  { path: "/statements", heading: "青色申告決算書", name: "statements" },
  { path: "/trial-balance", heading: "合計残高試算表", name: "trial-balance" },
  { path: "/pl", heading: "損益計算書", name: "pl" },
] as const;

for (const { path, heading, name } of SCREEN_TARGETS) {
  test(`visual: ${path} (screen)`, async ({ page }) => {
    await gotoReady(page, path, heading);
    await expect(page).toHaveScreenshot(`${name}-screen.png`, {
      fullPage: true,
    });
  });
}

/**
 * Print baselines — the regression target `print.css` (`@media print` in globals.css) was written
 * for but never tested. `emulateMedia({ media: "print" })` activates those rules (nav/controls
 * hidden, T-form kept side-by-side, darker submission-grade borders) so a change that breaks the
 * printed 提出資料 fails here even though the on-screen view still looks fine.
 */
const PRINT_TARGETS = [
  { path: "/statements", heading: "青色申告決算書", name: "statements" },
  { path: "/pl", heading: "損益計算書", name: "pl" },
] as const;

for (const { path, heading, name } of PRINT_TARGETS) {
  test(`visual: ${path} (print)`, async ({ page }) => {
    await gotoReady(page, path, heading);
    await page.emulateMedia({ media: "print" });
    await expect(page).toHaveScreenshot(`${name}-print.png`, {
      fullPage: true,
    });
  });
}

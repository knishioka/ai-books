import { expect, test } from "@playwright/test";

import { OWNER_STORAGE_STATE } from "../playwright.config";

/**
 * All-screens render smoke (issue #162). Authenticated as the owner, every one of the viewer's
 * ten report routes must return 200 and render its real content — asserted by the screen's
 * `<h1>` heading, which only appears when the page loaded data rather than an error banner.
 *
 * This is deliberately a *smoke* layer: it never asserts a figure (the golden cross-check owns
 * numeric correctness). It catches the orthogonal failure goldens cannot — a screen that fails to
 * render, an auth-gate regression that hides it, or a broken route.
 */
test.use({ storageState: OWNER_STORAGE_STATE });

const SCREENS = [
  { path: "/", heading: "ai-books viewer" },
  { path: "/trial-balance", heading: "合計残高試算表" },
  { path: "/monthly-trend", heading: "月次推移" },
  { path: "/journal", heading: "仕訳帳" },
  { path: "/ledger", heading: "総勘定元帳" },
  { path: "/pl", heading: "損益計算書" },
  { path: "/bs", heading: "貸借対照表" },
  { path: "/worksheet", heading: "精算表" },
  { path: "/statements", heading: "青色申告決算書" },
  { path: "/etax", heading: "e-Tax 取込データ" },
] as const;

for (const { path, heading } of SCREENS) {
  test(`renders ${path}`, async ({ page }) => {
    const response = await page.goto(path);
    expect(response?.status(), `${path} should respond 200`).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: heading }),
    ).toBeVisible();
  });
}

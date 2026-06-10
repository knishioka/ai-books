import { expect, test } from "@playwright/test";

import { OUTSIDER_EMAIL, OUTSIDER_PASSWORD } from "./users";

/**
 * Auth-gate fail-closed smoke (issue #162). These specs run with a **fresh, unauthenticated
 * context** (no storageState), so they prove the gate from the outside:
 *
 * 1. Every protected route bounces an anonymous visitor to `/login` and leaks no data first.
 * 2. A valid GoTrue user who is not the allowlisted owner is denied (allowlist, ADR-0008).
 *
 * This is the P0 invariant goldens can never cover: "公開したが認証が効いていない" must be
 * mechanically impossible.
 */
const PROTECTED_ROUTES = [
  "/",
  "/trial-balance",
  "/monthly-trend",
  "/journal",
  "/ledger",
  "/pl",
  "/bs",
  "/worksheet",
  "/statements",
  "/etax",
] as const;

test.describe("auth gate is fail closed", () => {
  for (const path of PROTECTED_ROUTES) {
    test(`unauthenticated ${path} redirects to /login with no data`, async ({
      page,
    }) => {
      await page.goto(path);
      // Fail closed: bounced to /login...
      await expect(page).toHaveURL(/\/login(\?|$)/);
      await expect(
        page.getByRole("heading", { level: 1, name: "ログイン" }),
      ).toBeVisible();
      // ...and not a single report table leaked before the redirect.
      await expect(page.locator("table")).toHaveCount(0);
    });
  }

  test("a valid but non-allowlisted user is rejected", async ({ page }) => {
    await page.goto("/login");
    await page.locator('input[name="email"]').fill(OUTSIDER_EMAIL);
    await page.locator('input[name="password"]').fill(OUTSIDER_PASSWORD);
    await page.getByRole("button", { name: "ログイン" }).click();

    // Authenticated yet unauthorized: the session is dropped and we land on the forbidden error.
    await expect(page).toHaveURL(/\/login\?error=forbidden/);
    await expect(
      page.getByText("このアカウントには閲覧権限がありません"),
    ).toBeVisible();
  });
});

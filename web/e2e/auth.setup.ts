import { expect, test as setup } from "@playwright/test";

import { OWNER_STORAGE_STATE } from "../playwright.config";
import {
  ensureUser,
  OUTSIDER_EMAIL,
  OUTSIDER_PASSWORD,
  OWNER_EMAIL,
  OWNER_PASSWORD,
} from "./users";

/**
 * Provision the GoTrue test identities and persist the authenticated owner session.
 *
 * Runs once before the smoke specs (Playwright `setup` project). It signs the owner in through
 * the **real login form** so the `@supabase/ssr` session cookie is written exactly as a browser
 * would, then saves it as `storageState` for the authenticated screens. The outsider is created
 * here too so `auth.spec.ts` can prove a valid-but-unauthorized user is rejected.
 */
setup("provision users and persist the owner session", async ({ page }) => {
  await ensureUser(OWNER_EMAIL, OWNER_PASSWORD);
  await ensureUser(OUTSIDER_EMAIL, OUTSIDER_PASSWORD);

  await page.goto("/login");
  await page.locator('input[name="email"]').fill(OWNER_EMAIL);
  await page.locator('input[name="password"]').fill(OWNER_PASSWORD);
  await page.getByRole("button", { name: "ログイン" }).click();

  // Sign-in redirects the owner to the chart-of-accounts home; the h1 proves we are authorized
  // and rendering data, not bounced back to /login.
  await expect(page).toHaveURL(/127\.0\.0\.1:\d+\/$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "ai-books viewer" }),
  ).toBeVisible();

  await page.context().storageState({ path: OWNER_STORAGE_STATE });
});

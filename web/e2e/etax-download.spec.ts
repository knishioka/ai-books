import { expect, test } from "@playwright/test";

import { OWNER_STORAGE_STATE } from "../playwright.config";
import { goldenEtaxBody } from "./helpers/golden";

/**
 * e-Tax download E2E (issue #164). The `/etax/download` route streams the 決算書 as the e-Tax
 * 取込データ (CSV / XML) — 確定数値 (秘密情報). This pins the two things the golden cross-check
 * cannot see because they live on the HTTP boundary, not in the data layer:
 *
 *   1. the **headers** — Content-Type, the download filename, and `Cache-Control: no-store` (the
 *      existing policy that keeps 確定数値 out of the browser cache; #25). A regression here leaks
 *      the file into cache or serves it inline.
 *   2. the **body** — byte-for-byte equal to the golden export rendered through the *same*
 *      `renderEtax` the route uses (see `goldenEtaxBody`). A render/mapping regression diverges.
 *
 * Pinned to `?fy=FY2025`, the year `etax_export.json` was frozen from. Requests go through the
 * authenticated owner context (`page.request` shares the storageState cookies), so the auth gate is
 * satisfied — an unauthenticated fetch would be redirected to /login and never reach this body.
 */
test.use({ storageState: OWNER_STORAGE_STATE });

const FY = "FY2025";

const CASES = [
  {
    format: "csv",
    contentType: "text/csv; charset=utf-8",
    filename: "etax_FY2025.csv",
  },
  {
    // The route serves XML under the e-Tax `.xtx` filename (the 取込 extension), by design.
    format: "xml",
    contentType: "application/xml; charset=utf-8",
    filename: "etax_FY2025.xtx",
  },
] as const;

for (const { format, contentType, filename } of CASES) {
  test(`/etax/download (${format}) returns golden body with no-store headers`, async ({
    page,
  }) => {
    const response = await page.request.get(
      `/etax/download?fy=${FY}&format=${format}`,
    );

    expect(response.status(), `${format} download should be 200`).toBe(200);

    const headers = response.headers();
    expect(headers["content-type"]).toBe(contentType);
    expect(headers["content-disposition"]).toBe(
      `attachment; filename="${filename}"`,
    );
    // 確定数値 must never linger in the browser cache (#25).
    expect(headers["cache-control"]).toBe("no-store");

    expect(await response.text()).toBe(goldenEtaxBody(format));
  });
}

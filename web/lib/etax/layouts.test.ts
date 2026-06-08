import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The web viewer bundles copies of the e-Tax 様式 layout JSONs under `./layouts/` (static
 * imports) because the Vercel deployment Root Directory is `web/` and cannot reach the Python
 * package at `../src/ai_books/etax/*.json`. This guards the copies against drift from the
 * source of truth — it runs in CI / locally (full repo present), not on Vercel.
 */
const FORMS = ["koa210", "koa220", "koa240"] as const;

describe("e-Tax layout JSON copies stay in sync with the Python package", () => {
  for (const form of FORMS) {
    it(`${form}_layout.json matches src/ai_books/etax`, () => {
      const file = `${form}_layout.json`;
      const webCopy = readFileSync(join(__dirname, "layouts", file), "utf8");
      const source = readFileSync(
        join(__dirname, "..", "..", "..", "src", "ai_books", "etax", file),
        "utf8",
      );
      expect(JSON.parse(webCopy)).toEqual(JSON.parse(source));
    });
  }
});

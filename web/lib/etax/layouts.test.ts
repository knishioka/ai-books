import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// ESM-safe directory of this test file (avoids relying on the CJS `__dirname` global).
const here = fileURLToPath(new URL(".", import.meta.url));

const FORMS = ["koa210", "koa220", "koa240"] as const;

function normalizeLineEndings(value: string): string {
  return value.replace(/\r\n/g, "\n");
}

describe("generated e-Tax layout JSONs stay in sync with the Python package", () => {
  for (const form of FORMS) {
    it(`${form}_layout.json is a byte-for-byte generated copy`, () => {
      const file = `${form}_layout.json`;
      const webCopy = readFileSync(join(here, "layouts", file), "utf8");
      const source = readFileSync(
        join(here, "..", "..", "..", "src", "ai_books", "etax", file),
        "utf8",
      );
      expect(normalizeLineEndings(webCopy)).toBe(normalizeLineEndings(source));
    });
  }
});

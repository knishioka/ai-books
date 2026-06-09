import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const root = process.cwd();

const tableFiles = [
  "app/page.tsx",
  "app/journal/page.tsx",
  "app/ledger/page.tsx",
  "app/trial-balance/page.tsx",
  "app/worksheet/page.tsx",
  "app/monthly-trend/page.tsx",
  "app/statements/page.tsx",
  "app/etax/page.tsx",
  "components/pl-table.tsx",
  "components/bs-tables.tsx",
];

const reportPages = [
  "app/page.tsx",
  "app/journal/page.tsx",
  "app/ledger/page.tsx",
  "app/trial-balance/page.tsx",
  "app/worksheet/page.tsx",
  "app/pl/page.tsx",
  "app/bs/page.tsx",
  "app/monthly-trend/page.tsx",
  "app/statements/page.tsx",
  "app/etax/page.tsx",
];

function readWebFile(relativePath: string): string {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("viewer accessibility markup", () => {
  it("gives every table header cell an explicit scope", () => {
    const missingScope = tableFiles.flatMap((file) => {
      const source = readWebFile(file);
      return [...source.matchAll(/<th\b(?![^>]*\bscope=)[^>]*>/gms)].map(
        (match) => `${file}: ${match[0]}`,
      );
    });

    expect(missingScope).toEqual([]);
  });

  it("marks the current route in the shared nav", () => {
    const nav = readWebFile("components/nav.tsx");

    expect(nav).toContain("usePathname");
    expect(nav).toContain('aria-current={isCurrentPath("/") ? "page"');
    expect(nav).toContain("isCurrentPath(route.href) ? \"page\"");
  });

  it("keeps wide journal and ledger tables horizontally scrollable", () => {
    expect(readWebFile("app/journal/page.tsx")).toContain(
      'className="card scroll-x"',
    );
    expect(readWebFile("app/ledger/page.tsx")).toContain(
      'className="scroll-x"',
    );
  });

  it("defines page-level metadata titles for viewer reports", () => {
    const missingMetadata = reportPages.filter(
      (file) => !readWebFile(file).includes("export const metadata"),
    );

    expect(missingMetadata).toEqual([]);
  });

  it("defines keyboard focus and current-nav styles", () => {
    const css = readWebFile("app/globals.css");

    expect(css).toContain(":focus-visible");
    expect(css).toContain('a[aria-current="page"]');
  });
});

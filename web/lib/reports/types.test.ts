import { describe, expect, it } from "vitest";

import {
  PL_ACCOUNT_TYPES,
  PL_CATEGORY_SECTION,
  PL_SECTIONS,
  STATEMENT_CATEGORY_ACCOUNT_TYPE,
  type StatementCategory,
} from "./types";

describe("PL_CATEGORY_SECTION", () => {
  it("derives a category→section-key entry for every category listed in PL_SECTIONS", () => {
    for (const [key, , categories] of PL_SECTIONS) {
      for (const category of categories) {
        expect(PL_CATEGORY_SECTION.get(category)).toBe(key);
      }
    }
  });

  it("routes the four 売上原価 categories to the cost_of_goods_sold section", () => {
    for (const category of [
      "cost_of_goods_sold",
      "manufacturing_materials",
      "manufacturing_labor",
      "manufacturing_overhead",
    ] as const) {
      expect(PL_CATEGORY_SECTION.get(category)).toBe("cost_of_goods_sold");
    }
  });

  it("does not classify a B/S category (網羅性ギャップが見える)", () => {
    expect(PL_CATEGORY_SECTION.get("current_assets")).toBeUndefined();
  });
});

describe("STATEMENT_CATEGORY_ACCOUNT_TYPE", () => {
  it("assigns an account type consistent with PL_ACCOUNT_TYPES for every PL category", () => {
    for (const [, , categories] of PL_SECTIONS) {
      for (const category of categories) {
        expect(
          PL_ACCOUNT_TYPES.has(STATEMENT_CATEGORY_ACCOUNT_TYPE[category]),
        ).toBe(true);
      }
    }
  });

  it("maps B/S categories to their balance-sheet account types", () => {
    const expected: Partial<Record<StatementCategory, string>> = {
      current_assets: "asset",
      fixed_assets: "asset",
      current_liabilities: "liability",
      fixed_liabilities: "liability",
      equity: "equity",
    };
    for (const [category, type] of Object.entries(expected)) {
      expect(
        STATEMENT_CATEGORY_ACCOUNT_TYPE[category as StatementCategory],
      ).toBe(type);
    }
  });
});

describe("PL_ACCOUNT_TYPES", () => {
  it("is exactly {revenue, expense}", () => {
    expect([...PL_ACCOUNT_TYPES].sort()).toEqual(["expense", "revenue"]);
  });
});

import { describe, expect, it } from "vitest";

import {
  ETAX_FORMAT_SPECS,
  getFormatSpec,
  LATEST_ETAX_VERSION,
  MISSING,
  resolveList,
  resolveScalar,
} from "./spec";

const SNAPSHOT = {
  profit_and_loss: { sales: { subtotal: "420000.00" }, net_income: null },
  balance_sheet: {
    assets: [
      { lines: [{ code: "1110", balance: "10.00" }] },
      { lines: [{ code: "1141", balance: "20.00" }] },
    ],
    liabilities: [],
  },
  monthly: { rows: [{ month: "2025-01" }, { month: "2025-02" }] },
};

describe("resolveScalar", () => {
  it("descends a dot-path to a present value", () => {
    expect(resolveScalar(SNAPSHOT, "profit_and_loss.sales.subtotal")).toBe(
      "420000.00",
    );
  });

  it("returns a present null distinctly from MISSING", () => {
    expect(resolveScalar(SNAPSHOT, "profit_and_loss.net_income")).toBeNull();
  });

  it("returns MISSING for an absent key", () => {
    expect(resolveScalar(SNAPSHOT, "profit_and_loss.gross_profit")).toBe(
      MISSING,
    );
    expect(resolveScalar(SNAPSHOT, "nope.at.all")).toBe(MISSING);
  });

  it("returns MISSING when descending into a non-object", () => {
    expect(
      resolveScalar(SNAPSHOT, "profit_and_loss.sales.subtotal.deeper"),
    ).toBe(MISSING);
  });
});

describe("resolveList", () => {
  it("flattens nested lists across `[]` segments", () => {
    expect(resolveList(SNAPSHOT, "balance_sheet.assets[].lines")).toEqual([
      { code: "1110", balance: "10.00" },
      { code: "1141", balance: "20.00" },
    ]);
  });

  it("resolves a plain (non-flattened) list path", () => {
    expect(resolveList(SNAPSHOT, "monthly.rows")).toEqual([
      { month: "2025-01" },
      { month: "2025-02" },
    ]);
  });

  it("returns an empty list for an absent path", () => {
    expect(resolveList(SNAPSHOT, "balance_sheet.equity[].lines")).toEqual([]);
    expect(resolveList(SNAPSHOT, "balance_sheet.liabilities[].lines")).toEqual(
      [],
    );
  });

  it("filters out non-object rows", () => {
    expect(resolveList({ rows: ["x", 1, null, { a: 1 }] }, "rows")).toEqual([
      { a: 1 },
    ]);
  });

  it("returns an empty list when the path is not a list", () => {
    expect(resolveList(SNAPSHOT, "profit_and_loss.sales")).toEqual([]);
  });
});

describe("getFormatSpec", () => {
  it("returns the registered spec for a known version", () => {
    const spec = getFormatSpec(LATEST_ETAX_VERSION);
    expect(spec.version).toBe("2025");
    expect(spec.formId).toBe("青色申告決算書(一般用)");
    expect(spec.items.length).toBeGreaterThan(0);
  });

  it("keeps the synthetic spec registered off the year axis (#78)", () => {
    expect(LATEST_ETAX_VERSION).toBe("2025");
    const synthetic = getFormatSpec("synthetic");
    expect(synthetic.formId).toBe("青色申告決算書(一般用・合成)");
  });

  it("LATEST_ETAX_VERSION points at a registered spec", () => {
    expect(ETAX_FORMAT_SPECS[LATEST_ETAX_VERSION]).toBeDefined();
  });

  it("throws (listing known versions) for an unknown version", () => {
    expect(() => getFormatSpec("1999")).toThrowError(
      /unknown e-Tax format version "1999".*2025/,
    );
  });
});

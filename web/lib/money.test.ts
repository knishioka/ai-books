import { describe, expect, it } from "vitest";

import {
  formatMoney,
  parseAmount,
  parseMoney,
  sumMoney,
  ZERO,
  type Money,
} from "./money";

describe("parseMoney", () => {
  it("parses a numeric(18,2) string into 銭", () => {
    expect(parseMoney("420000.00")).toBe(42000000n);
    expect(parseMoney("0")).toBe(0n);
    expect(parseMoney("0.00")).toBe(0n);
    expect(parseMoney("1.5")).toBe(150n);
    expect(parseMoney("1.05")).toBe(105n);
  });

  it("carries the sign", () => {
    expect(parseMoney("-350000.00")).toBe(-35000000n);
    expect(parseMoney("-0.01")).toBe(-1n);
  });

  it("pads a short fractional part to two digits", () => {
    expect(parseMoney("7.1")).toBe(710n);
    expect(parseMoney("7.")).toBe(700n);
  });

  it("truncates (does not round) a long fractional part to two digits", () => {
    expect(parseMoney("1.999")).toBe(199n);
    expect(parseMoney("1.005")).toBe(100n);
  });

  it("treats a bare integer as whole yen", () => {
    expect(parseMoney("1650000")).toBe(165000000n);
  });

  it("scales a bigint as whole yen (e-Tax integer path)", () => {
    expect(parseMoney(1650000n)).toBe(165000000n);
    expect(parseMoney(0n)).toBe(0n);
    expect(parseMoney(-5n)).toBe(-500n);
  });

  it("accepts a JS number", () => {
    expect(parseMoney(300000)).toBe(30000000n);
  });

  it("trims surrounding whitespace", () => {
    expect(parseMoney("  420000.00  ")).toBe(42000000n);
  });

  it("exposes parseAmount as an alias", () => {
    expect(parseAmount).toBe(parseMoney);
  });
});

describe("formatMoney", () => {
  it("renders the fixed 2-dp numeric(18,2) shape", () => {
    expect(formatMoney(42000000n)).toBe("420000.00");
    expect(formatMoney(0n)).toBe("0.00");
    expect(formatMoney(150n)).toBe("1.50");
    expect(formatMoney(5n)).toBe("0.05");
  });

  it("renders negatives with a single leading minus", () => {
    expect(formatMoney(-35000000n)).toBe("-350000.00");
    expect(formatMoney(-1n)).toBe("-0.01");
  });

  it("never emits -0.00 for zero", () => {
    expect(formatMoney(ZERO)).toBe("0.00");
    expect(formatMoney(0n)).not.toContain("-");
  });

  it("does not use exponential notation for large amounts", () => {
    expect(formatMoney(30000000000n)).toBe("300000000.00");
  });

  it("round-trips with parseMoney", () => {
    for (const text of ["420000.00", "-350000.00", "0.00", "1.05", "0.01"]) {
      expect(formatMoney(parseMoney(text))).toBe(text);
    }
  });
});

describe("sumMoney", () => {
  it("sums exactly", () => {
    expect(sumMoney([100n, 200n, -50n])).toBe(250n);
  });

  it("is ZERO for an empty list", () => {
    const empty: Money[] = [];
    expect(sumMoney(empty)).toBe(ZERO);
  });

  it("stays exact past the float-safe integer range", () => {
    const big = 90071992547409910n; // > Number.MAX_SAFE_INTEGER 銭
    expect(sumMoney([big, big])).toBe(180143985094819820n);
  });
});

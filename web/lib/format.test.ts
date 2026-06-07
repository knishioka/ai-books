import { describe, expect, it } from "vitest";

import { formatAmount, isNegative, sideLabel } from "./format";

describe("formatAmount", () => {
  it("groups the integer part and drops a trailing .00", () => {
    expect(formatAmount("300000.00")).toBe("¥300,000");
    expect(formatAmount("0.00")).toBe("¥0");
    expect(formatAmount("999.00")).toBe("¥999");
    expect(formatAmount("1000.00")).toBe("¥1,000");
    expect(formatAmount("1234567.00")).toBe("¥1,234,567");
  });

  it("preserves a non-zero 端数", () => {
    expect(formatAmount("1234.50")).toBe("¥1,234.50");
    expect(formatAmount("0.05")).toBe("¥0.05");
  });

  it("renders negatives with the accounting 三角 △ (no minus, no ¥)", () => {
    expect(formatAmount("-350000.00")).toBe("△350,000");
    expect(formatAmount("-580500.00")).toBe("△580,500");
    expect(formatAmount("-1234.50")).toBe("△1,234.50");
  });

  it("tolerates an amount with no fractional part", () => {
    expect(formatAmount("500")).toBe("¥500");
  });

  it("drops an all-zero 端数 regardless of digit count", () => {
    expect(formatAmount("300000.0")).toBe("¥300,000");
    expect(formatAmount("300000.000")).toBe("¥300,000");
    expect(formatAmount("-580500.0")).toBe("△580,500");
  });
});

describe("isNegative", () => {
  it("detects the leading minus", () => {
    expect(isNegative("-1.00")).toBe(true);
    expect(isNegative("0.00")).toBe(false);
    expect(isNegative("1.00")).toBe(false);
  });
});

describe("sideLabel", () => {
  it("maps debit/credit to 借方/貸方", () => {
    expect(sideLabel("debit")).toBe("借方");
    expect(sideLabel("credit")).toBe("貸方");
  });
});

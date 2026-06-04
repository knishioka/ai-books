import { describe, expect, it } from "vitest";

import { balanceFromTotals, signedDelta } from "./ledger";

describe("balanceFromTotals", () => {
  it("signs a debit-normal account as 借方 − 貸方 (資産/費用)", () => {
    expect(balanceFromTotals(300n, 100n, "debit")).toBe(200n);
  });

  it("signs a credit-normal account as 貸方 − 借方 (負債/純資産/収益)", () => {
    expect(balanceFromTotals(100n, 300n, "credit")).toBe(200n);
  });

  it("returns a negative balance when the account sits opposite its normal side", () => {
    expect(balanceFromTotals(100n, 300n, "debit")).toBe(-200n);
    expect(balanceFromTotals(300n, 100n, "credit")).toBe(-200n);
  });

  it("handles one-sided activity (片側のみ)", () => {
    expect(balanceFromTotals(500n, 0n, "debit")).toBe(500n);
    expect(balanceFromTotals(0n, 500n, "credit")).toBe(500n);
    expect(balanceFromTotals(0n, 500n, "debit")).toBe(-500n);
  });

  it("is zero when 借方 == 貸方", () => {
    expect(balanceFromTotals(750n, 750n, "debit")).toBe(0n);
    expect(balanceFromTotals(750n, 750n, "credit")).toBe(0n);
  });
});

describe("signedDelta", () => {
  it("is positive when the line's side matches the normal side", () => {
    expect(signedDelta("debit", "debit", 100n)).toBe(100n);
    expect(signedDelta("credit", "credit", 100n)).toBe(100n);
  });

  it("is negative when the line works against the normal side", () => {
    expect(signedDelta("credit", "debit", 100n)).toBe(-100n);
    expect(signedDelta("debit", "credit", 100n)).toBe(-100n);
  });
});

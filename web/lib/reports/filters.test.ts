import { describe, expect, it } from "vitest";

import {
  normalizeAccountCodeParam,
  normalizeFiscalYearParam,
} from "./filters";

describe("normalizeAccountCodeParam", () => {
  it("keeps compact account-code values", () => {
    expect(normalizeAccountCodeParam("1141")).toBe("1141");
    expect(normalizeAccountCodeParam("cash_main-1")).toBe("cash_main-1");
  });

  it("collapses empty or absent values to null", () => {
    expect(normalizeAccountCodeParam(undefined)).toBeNull();
    expect(normalizeAccountCodeParam(null)).toBeNull();
    expect(normalizeAccountCodeParam("")).toBeNull();
  });

  it("rejects values that would create unsafe cache-key cardinality", () => {
    expect(normalizeAccountCodeParam("あいう")).toBeNull();
    expect(normalizeAccountCodeParam("1141?x=1")).toBeNull();
    expect(normalizeAccountCodeParam("1".repeat(33))).toBeNull();
  });

  it("uses the first repeated account-code value", () => {
    expect(normalizeAccountCodeParam(["1141", "1142"])).toBe("1141");
  });
});

describe("normalizeFiscalYearParam", () => {
  it("keeps canonical fiscal-year values", () => {
    expect(normalizeFiscalYearParam("FY2025")).toBe("FY2025");
    expect(normalizeFiscalYearParam(["FY2025", "FY2026"])).toBe("FY2025");
  });

  it("collapses malformed fiscal years to the default-year bucket", () => {
    expect(normalizeFiscalYearParam(undefined)).toBeNull();
    expect(normalizeFiscalYearParam("")).toBeNull();
    expect(normalizeFiscalYearParam("2025")).toBeNull();
    expect(normalizeFiscalYearParam("FY2025-extra")).toBeNull();
    expect(normalizeFiscalYearParam("FY" + "1".repeat(64))).toBeNull();
  });
});

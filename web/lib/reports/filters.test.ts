import { describe, expect, it } from "vitest";

import { normalizeAccountCodeParam } from "./filters";

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
});

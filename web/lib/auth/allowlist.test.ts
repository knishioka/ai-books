import { describe, expect, it } from "vitest";

import { isAllowedEmail, safeNextPath } from "./allowlist";

describe("isAllowedEmail", () => {
  it("allows any authenticated email when no allowlist is configured", () => {
    expect(isAllowedEmail("anyone@example.com", undefined)).toBe(true);
    expect(isAllowedEmail("anyone@example.com", null)).toBe(true);
    expect(isAllowedEmail("anyone@example.com", "")).toBe(true);
    expect(isAllowedEmail("anyone@example.com", "   ")).toBe(true);
  });

  it("authorizes only the configured owner when an allowlist is set", () => {
    expect(isAllowedEmail("owner@example.com", "owner@example.com")).toBe(true);
    expect(isAllowedEmail("intruder@example.com", "owner@example.com")).toBe(
      false,
    );
  });

  it("matches case-insensitively and trims surrounding whitespace", () => {
    expect(isAllowedEmail("Owner@Example.COM", "owner@example.com")).toBe(true);
    expect(isAllowedEmail("  owner@example.com  ", " owner@example.com ")).toBe(
      true,
    );
  });

  it("denies a missing email when an allowlist is set (fail closed)", () => {
    expect(isAllowedEmail(null, "owner@example.com")).toBe(false);
    expect(isAllowedEmail(undefined, "owner@example.com")).toBe(false);
    expect(isAllowedEmail("", "owner@example.com")).toBe(false);
    expect(isAllowedEmail("   ", "owner@example.com")).toBe(false);
  });
});

describe("safeNextPath", () => {
  it("keeps same-origin absolute paths (with query/fragment)", () => {
    expect(safeNextPath("/pl")).toBe("/pl");
    expect(safeNextPath("/ledger?fy=FY2025")).toBe("/ledger?fy=FY2025");
    expect(safeNextPath("/")).toBe("/");
  });

  it("falls back to / for missing or non-path values", () => {
    expect(safeNextPath(undefined)).toBe("/");
    expect(safeNextPath(null)).toBe("/");
    expect(safeNextPath("")).toBe("/");
    expect(safeNextPath("pl")).toBe("/");
  });

  it("rejects open-redirect attempts", () => {
    expect(safeNextPath("//evil.com")).toBe("/");
    expect(safeNextPath("https://evil.com")).toBe("/");
    expect(safeNextPath("/\\evil.com")).toBe("/");
    expect(safeNextPath("\\/evil.com")).toBe("/");
  });
});

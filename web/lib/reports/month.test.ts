import { describe, expect, it } from "vitest";

import { monthLabels } from "./month";

describe("monthLabels", () => {
  it("returns a single label when start and end share a month", () => {
    expect(monthLabels("2025-03-15", "2025-03-31")).toEqual(["2025-03"]);
  });

  it("tiles a January-start calendar year into 12 months", () => {
    const labels = monthLabels("2025-01-01", "2025-12-31");
    expect(labels).toHaveLength(12);
    expect(labels[0]).toBe("2025-01");
    expect(labels[11]).toBe("2025-12");
  });

  it("tiles an April→March fiscal year cleanly across the year boundary", () => {
    const labels = monthLabels("2025-04-01", "2026-03-31");
    expect(labels).toHaveLength(12);
    expect(labels[0]).toBe("2025-04");
    expect(labels[8]).toBe("2025-12");
    expect(labels[9]).toBe("2026-01");
    expect(labels[11]).toBe("2026-03");
  });

  it("rolls the year over at December (月跨ぎ)", () => {
    expect(monthLabels("2025-12-01", "2026-01-31")).toEqual([
      "2025-12",
      "2026-01",
    ]);
  });

  it("ignores the day-of-month — only the period's months matter", () => {
    expect(monthLabels("2025-02-28", "2025-04-01")).toEqual([
      "2025-02",
      "2025-03",
      "2025-04",
    ]);
  });

  it("returns an empty list when end precedes start (空期間)", () => {
    expect(monthLabels("2025-06-01", "2025-03-31")).toEqual([]);
  });

  it("zero-pads month numbers", () => {
    expect(monthLabels("2025-09-01", "2025-10-31")).toEqual([
      "2025-09",
      "2025-10",
    ]);
  });
});

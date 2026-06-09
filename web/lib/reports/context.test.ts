import type { Sql } from "postgres";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FiscalYear } from "./fiscal-year";

const mocks = vi.hoisted(() => ({
  sql: undefined as Sql | undefined,
}));

vi.mock("server-only", () => ({}));

vi.mock("next/cache", () => ({
  unstable_cache:
    <T extends (...args: never[]) => unknown>(fn: T) =>
    fn,
}));

vi.mock("../db", () => ({
  tryQuery: async <T>(query: (sql: Sql) => Promise<T>) => {
    if (!mocks.sql) {
      return { ok: false, error: "missing test sql" };
    }
    return { ok: true, data: await query(mocks.sql) };
  },
}));

import { loadReport } from "./context";

function fakeSql<T>(rows: T[]): {
  sql: Sql;
  queryCount: () => number;
} {
  let queries = 0;
  const tag = () => ({
    then(
      onFulfilled: (value: T[]) => unknown,
      onRejected?: (reason: unknown) => unknown,
    ) {
      queries += 1;
      return Promise.resolve(rows).then(onFulfilled, onRejected);
    },
  });
  return { sql: tag as unknown as Sql, queryCount: () => queries };
}

describe("loadReport", () => {
  const latestFiscalYear: FiscalYear = {
    name: "FY2025",
    start_date: "2025-01-01",
    end_date: "2025-12-31",
  };
  const olderFiscalYear: FiscalYear = {
    name: "FY2024",
    start_date: "2024-01-01",
    end_date: "2024-12-31",
  };

  beforeEach(() => {
    mocks.sql = undefined;
  });

  it("uses the newest listed fiscal year without a duplicate default lookup", async () => {
    const fake = fakeSql([latestFiscalYear, olderFiscalYear]);
    mocks.sql = fake.sql;
    const build = vi.fn(async () => "report");

    const result = await loadReport("test-report", undefined, build);

    expect(result).toEqual({
      ok: true,
      data: {
        fiscalYear: latestFiscalYear,
        fiscalYears: [latestFiscalYear, olderFiscalYear],
        data: "report",
      },
    });
    expect(build).toHaveBeenCalledWith(fake.sql, latestFiscalYear);
    expect(fake.queryCount()).toBe(1);
  });

  it("uses a requested fiscal year from the already listed years", async () => {
    const fake = fakeSql([latestFiscalYear, olderFiscalYear]);
    mocks.sql = fake.sql;
    const build = vi.fn(async () => "report");

    const result = await loadReport("test-report", "FY2024", build);

    expect(result.ok).toBe(true);
    expect(build).toHaveBeenCalledWith(fake.sql, olderFiscalYear);
    expect(fake.queryCount()).toBe(1);
  });

  it("returns an error for bounded but unknown requested fiscal years", async () => {
    const fake = fakeSql([latestFiscalYear]);
    mocks.sql = fake.sql;
    const build = vi.fn(async () => "report");

    const result = await loadReport("test-report", "FY2024", build);

    expect(result).toEqual({
      ok: false,
      error: "会計年度 FY2024 は登録されていません。",
    });
    expect(build).not.toHaveBeenCalled();
    expect(fake.queryCount()).toBe(1);
  });
});

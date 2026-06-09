import type { Sql } from "postgres";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FiscalYear } from "./fiscal-year";

const mocks = vi.hoisted(() => ({
  resolveFiscalYear: vi.fn(),
  sql: undefined as Sql | undefined,
}));

vi.mock("server-only", () => ({}));

vi.mock("../db", () => ({
  tryQuery: async <T>(query: (sql: Sql) => Promise<T>) => {
    if (!mocks.sql) {
      return { ok: false, error: "missing test sql" };
    }
    return { ok: true, data: await query(mocks.sql) };
  },
}));

vi.mock("./fiscal-year", () => ({
  resolveFiscalYear: mocks.resolveFiscalYear,
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
    mocks.resolveFiscalYear.mockReset();
    mocks.sql = undefined;
  });

  it("uses the newest listed fiscal year without a duplicate default lookup", async () => {
    const fake = fakeSql([latestFiscalYear, olderFiscalYear]);
    mocks.sql = fake.sql;
    const build = vi.fn(async () => "report");

    const result = await loadReport(undefined, build);

    expect(result).toEqual({
      ok: true,
      data: {
        fiscalYear: latestFiscalYear,
        fiscalYears: [latestFiscalYear, olderFiscalYear],
        data: "report",
      },
    });
    expect(mocks.resolveFiscalYear).not.toHaveBeenCalled();
    expect(build).toHaveBeenCalledWith(fake.sql, latestFiscalYear);
    expect(fake.queryCount()).toBe(1);
  });

  it("delegates requested fiscal years to the existing resolver", async () => {
    const fake = fakeSql([latestFiscalYear, olderFiscalYear]);
    mocks.sql = fake.sql;
    mocks.resolveFiscalYear.mockResolvedValue(olderFiscalYear);
    const build = vi.fn(async () => "report");

    const result = await loadReport("FY2024", build);

    expect(result.ok).toBe(true);
    expect(mocks.resolveFiscalYear).toHaveBeenCalledWith(fake.sql, "FY2024");
    expect(build).toHaveBeenCalledWith(fake.sql, olderFiscalYear);
    expect(fake.queryCount()).toBe(1);
  });
});

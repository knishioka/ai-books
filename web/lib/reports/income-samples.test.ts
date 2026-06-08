import type { Sql } from "postgres";
import { describe, expect, it } from "vitest";

import { buildSampleEtaxExport, sampleEtaxKind } from "../etax/sample";
import { fetchAgriculturalIncome } from "./agricultural-income";
import { fetchRealEstateIncome } from "./real-estate-income";

function fakeSql(...resultSets: Record<string, unknown>[][]): Sql {
  let i = 0;
  const tag = () => ({
    then(
      onFulfilled: (rows: Record<string, unknown>[]) => unknown,
      onRejected?: (reason: unknown) => unknown,
    ) {
      const rows = i < resultSets.length ? resultSets[i++] : [];
      return Promise.resolve(rows).then(onFulfilled, onRejected);
    },
  });
  return tag as unknown as Sql;
}

const REAL_ESTATE_BALANCES = [
  {
    code: "4210",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "1300000.00",
  },
  {
    code: "4220",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "960000.00",
  },
  {
    code: "7250",
    normal_balance: "debit",
    debit_total: "240000.00",
    credit_total: "0.00",
  },
  {
    code: "2510",
    normal_balance: "credit",
    debit_total: "500000.00",
    credit_total: "8000000.00",
  },
  {
    code: "8210",
    normal_balance: "debit",
    debit_total: "80000.00",
    credit_total: "0.00",
  },
];

const AGRICULTURAL_BALANCES = [
  {
    code: "1185",
    normal_balance: "debit",
    debit_total: "250000.00",
    credit_total: "0.00",
  },
  {
    code: "4310",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "1100000.00",
  },
  {
    code: "4320",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "400000.00",
  },
  {
    code: "4330",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "500000.00",
  },
  {
    code: "4340",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "1800000.00",
  },
  {
    code: "4350",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "140000.00",
  },
  {
    code: "4360",
    normal_balance: "credit",
    debit_total: "0.00",
    credit_total: "200000.00",
  },
];

describe("public sample income snapshots", () => {
  it("builds the KOA220 real-estate snapshot from ledger balances and fixture metadata", async () => {
    const snapshot = await fetchRealEstateIncome(fakeSql(REAL_ESTATE_BALANCES), {
      fiscalYear: "FY2023-KOA220",
      start: "2023-01-01",
      end: "2023-12-31",
    });
    expect(snapshot.rental_income.gross_income).toBe("2260000.00");
    expect(snapshot.rent_paid.rent_total).toBe("240000.00");
    expect(snapshot.loan_interest.year_end_balance_total).toBe("7500000.00");
  });

  it("builds the KOA240 agricultural snapshot from category balances and fixture metadata", async () => {
    const snapshot = await fetchAgriculturalIncome(fakeSql(AGRICULTURAL_BALANCES), {
      fiscalYear: "FY2024-KOA240",
      start: "2024-01-01",
      end: "2024-12-31",
    });
    expect(snapshot.farm_products.sales_total).toBe("2000000.00");
    expect(snapshot.livestock.sales_total).toBe("1800000.00");
    expect(snapshot.income.gross_income).toBe("4190000.00");
  });
});

describe("buildSampleEtaxExport", () => {
  it("routes sample fiscal years to KOA210/KOA220/KOA240 builders", async () => {
    expect(sampleEtaxKind("FY2025")).toBe("general");
    expect(sampleEtaxKind("FY2023-KOA220")).toBe("real_estate");
    expect(sampleEtaxKind("FY2024-KOA240")).toBe("agricultural");

    const koa220 = await buildSampleEtaxExport(fakeSql(REAL_ESTATE_BALANCES), {
      name: "FY2023-KOA220",
      start_date: "2023-01-01",
      end_date: "2023-12-31",
    });
    const koa240 = await buildSampleEtaxExport(fakeSql(AGRICULTURAL_BALANCES), {
      name: "FY2024-KOA240",
      start_date: "2024-01-01",
      end_date: "2024-12-31",
    });
    expect(koa220.formId).toBe("青色申告決算書(不動産所得用)");
    expect(koa220.records.some((record) => record.itemCode === "ANF00570")).toBe(true);
    expect(koa240.formId).toBe("青色申告決算書(農業所得用)");
    expect(koa240.records.some((record) => record.itemCode === "APF00180")).toBe(true);
  });
});

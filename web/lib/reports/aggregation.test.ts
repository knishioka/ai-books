import type { Sql } from "postgres";
import { describe, expect, it } from "vitest";

import { fetchBalanceSheet } from "./balance-sheet";
import { fetchFinancialStatements } from "./financial-statements";
import { fetchProfitAndLoss } from "./profit-and-loss";
import { fetchTrialBalance } from "./trial-balance";
import { fetchWorksheet } from "./worksheet";

/**
 * A stand-in for the `postgres` `sql` tag that returns canned result sets instead of touching a
 * database — so the in-memory aggregation (符号則・段階利益・科目振り分け・月次タイリング) can be
 * pinned without a DB. Each `await sql\`…\`` consumes the next result set in order; nested
 * fragments (e.g. `statusFilter(sql, …)`) are created but never awaited, so they do not consume a
 * result set. The real numbers still flow through the DB-backed golden cross-check (#17/#25).
 */
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

const flushMicrotasks = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

describe("fetchTrialBalance", () => {
  it("signs each account and foots both columns", async () => {
    const sql = fakeSql([
      {
        code: "1110",
        name: "現金",
        normal_balance: "debit",
        debit_total: "300000.00",
        credit_total: "100000.00",
      },
      {
        code: "4110",
        name: "売上高",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "420000.00",
      },
    ]);
    const tb = await fetchTrialBalance(sql, { fiscalYear: "FY2025" });
    expect(tb.rows).toEqual([
      {
        code: "1110",
        name: "現金",
        debit_total: "300000.00",
        credit_total: "100000.00",
        balance: "200000.00",
      },
      {
        code: "4110",
        name: "売上高",
        debit_total: "0.00",
        credit_total: "420000.00",
        balance: "420000.00",
      },
    ]);
    expect(tb.total_debit).toBe("300000.00");
    expect(tb.total_credit).toBe("520000.00");
  });

  it("foots to zero on an empty fiscal year (空 FY)", async () => {
    const tb = await fetchTrialBalance(fakeSql([]), { fiscalYear: "FY2025" });
    expect(tb.rows).toEqual([]);
    expect(tb.total_debit).toBe("0.00");
    expect(tb.total_credit).toBe("0.00");
  });
});

describe("fetchProfitAndLoss", () => {
  const rows = [
    {
      code: "4110",
      name: "売上高",
      account_type: "revenue",
      statement_category: "sales",
      normal_balance: "credit",
      debit_total: "0.00",
      credit_total: "420000.00",
    },
    {
      code: "5110",
      name: "仕入高",
      account_type: "expense",
      statement_category: "cost_of_goods_sold",
      normal_balance: "debit",
      debit_total: "100000.00",
      credit_total: "0.00",
    },
    {
      code: "7110",
      name: "給料賃金",
      account_type: "expense",
      statement_category: "selling_admin_expenses",
      normal_balance: "debit",
      debit_total: "20000.00",
      credit_total: "0.00",
    },
    {
      code: "9999",
      name: "謎勘定",
      account_type: "expense",
      statement_category: null,
      normal_balance: "debit",
      debit_total: "5000.00",
      credit_total: "0.00",
    },
  ];

  it("buckets sections and derives the staged 段階利益", async () => {
    const pl = await fetchProfitAndLoss(fakeSql(rows), {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-12-31",
    });
    expect(pl.sales.subtotal).toBe("420000.00");
    expect(pl.cost_of_goods_sold.subtotal).toBe("100000.00");
    expect(pl.gross_profit).toBe("320000.00");
    expect(pl.selling_admin_expenses.subtotal).toBe("20000.00");
    expect(pl.operating_income).toBe("300000.00");
    expect(pl.ordinary_income).toBe("300000.00");
    expect(pl.net_income).toBe("300000.00");
  });

  it("collects a 収益/費用 account with no 表示区分 into unclassified (網羅性)", async () => {
    const pl = await fetchProfitAndLoss(fakeSql(rows), {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-12-31",
    });
    expect(pl.unclassified).toEqual([
      { code: "9999", name: "謎勘定", category: null, amount: "5000.00" },
    ]);
  });

  it("is all-zero on an empty fiscal year", async () => {
    const pl = await fetchProfitAndLoss(fakeSql([]), {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-12-31",
    });
    expect(pl.sales.subtotal).toBe("0.00");
    expect(pl.gross_profit).toBe("0.00");
    expect(pl.net_income).toBe("0.00");
    expect(pl.unclassified).toEqual([]);
  });
});

describe("fetchBalanceSheet", () => {
  it("sections 資産/負債/純資産, folds 収益−費用 into 当期純利益, and closes 純資産", async () => {
    const sql = fakeSql([
      {
        code: "1110",
        name: "現金",
        statement_category: "current_assets",
        normal_balance: "debit",
        debit_total: "500000.00",
        credit_total: "0.00",
      },
      {
        code: "2110",
        name: "買掛金",
        statement_category: "current_liabilities",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "100000.00",
      },
      {
        code: "3110",
        name: "元入金",
        statement_category: "equity",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "100000.00",
      },
      {
        code: "4110",
        name: "売上高",
        statement_category: "sales",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "420000.00",
      },
      {
        code: "5110",
        name: "仕入高",
        statement_category: "cost_of_goods_sold",
        normal_balance: "debit",
        debit_total: "120000.00",
        credit_total: "0.00",
      },
    ]);
    const bs = await fetchBalanceSheet(sql, { asOf: "2025-12-31" });
    expect(bs.total_assets).toBe("500000.00");
    expect(bs.total_liabilities).toBe("100000.00");
    expect(bs.net_income).toBe("300000.00"); // 420000 収益 − 120000 費用
    expect(bs.total_equity).toBe("400000.00"); // 元入金 100000 + 当期純利益 300000
  });

  it("drops a B/S account that nets to exactly zero", async () => {
    const sql = fakeSql([
      {
        code: "1110",
        name: "現金",
        statement_category: "current_assets",
        normal_balance: "debit",
        debit_total: "5000.00",
        credit_total: "5000.00",
      },
    ]);
    const bs = await fetchBalanceSheet(sql, { asOf: "2025-12-31" });
    const currentAssets = bs.assets.find(
      (s) => s.category === "current_assets",
    );
    expect(currentAssets?.lines).toEqual([]);
    expect(bs.total_assets).toBe("0.00");
  });

  it("throws loudly on a touched account with no 表示区分 (貸借一致を壊す前に)", async () => {
    const sql = fakeSql([
      {
        code: "1110",
        name: "謎資産",
        statement_category: null,
        normal_balance: "debit",
        debit_total: "1000.00",
        credit_total: "0.00",
      },
    ]);
    await expect(
      fetchBalanceSheet(sql, { asOf: "2025-12-31" }),
    ).rejects.toThrowError(/no statement_category/);
  });
});

describe("fetchWorksheet", () => {
  it("routes the adjusted net to PL/BS columns on the side its sign falls on", async () => {
    const sql = fakeSql([
      {
        code: "1110",
        name: "現金",
        account_type: "asset",
        unadjusted_debit: "500000.00",
        unadjusted_credit: "0.00",
        adjustment_debit: "0.00",
        adjustment_credit: "0.00",
      },
      {
        code: "4110",
        name: "売上高",
        account_type: "revenue",
        unadjusted_debit: "0.00",
        unadjusted_credit: "420000.00",
        adjustment_debit: "0.00",
        adjustment_credit: "0.00",
      },
      {
        code: "5110",
        name: "仕入高",
        account_type: "expense",
        unadjusted_debit: "100000.00",
        unadjusted_credit: "0.00",
        adjustment_debit: "5000.00",
        adjustment_credit: "0.00",
      },
    ]);
    const ws = await fetchWorksheet(sql, {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-12-31",
    });

    const cash = ws.rows.find((r) => r.code === "1110")!;
    expect(cash.trial_debit).toBe("500000.00");
    expect(cash.bs_debit).toBe("500000.00");
    expect(cash.pl_debit).toBe("0.00");

    const sales = ws.rows.find((r) => r.code === "4110")!;
    expect(sales.trial_credit).toBe("420000.00");
    expect(sales.pl_credit).toBe("420000.00");
    expect(sales.bs_credit).toBe("0.00");

    const cogs = ws.rows.find((r) => r.code === "5110")!;
    expect(cogs.trial_debit).toBe("100000.00"); // 残高試算表 = 修正記入前
    expect(cogs.adjustment_debit).toBe("5000.00");
    expect(cogs.pl_debit).toBe("105000.00"); // 損益計算書欄 = 修正記入後

    // 当期純利益 = 収益計 − 費用計
    expect(ws.pl_credit_total).toBe("420000.00");
    expect(ws.pl_debit_total).toBe("105000.00");
    expect(ws.net_income).toBe("315000.00");
    expect(ws.bs_debit_total).toBe("500000.00");
  });
});

describe("fetchFinancialStatements", () => {
  it("starts independent report queries before waiting for the PL", async () => {
    const started: string[] = [];
    const pending = new Map<string, (rows: Record<string, unknown>[]) => void>();
    const sql = ((strings: TemplateStringsArray) => {
      const query = strings.join("");
      if (query.includes("CASE WHEN")) {
        return {};
      }

      let kind: string;
      if (query.includes("a.account_type IN ('revenue', 'expense')")) {
        kind = "profit-and-loss";
      } else if (
        query.includes("a.statement_category = 'sales'") &&
        query.includes("a.name LIKE")
      ) {
        kind = "monthly-sales-purchases";
      } else if (query.includes("a.statement_category = 'fixed_assets'")) {
        kind = "depreciation";
      } else {
        kind = "balance-sheet";
      }

      started.push(kind);
      return new Promise<Record<string, unknown>[]>((resolve) => {
        pending.set(kind, resolve);
      });
    }) as unknown as Sql;

    const fsPromise = fetchFinancialStatements(sql, {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-03-31",
    });
    await flushMicrotasks();

    expect(started).toEqual([
      "profit-and-loss",
      "balance-sheet",
      "monthly-sales-purchases",
    ]);
    expect(started).not.toContain("depreciation");

    pending.get("profit-and-loss")!([]);
    await flushMicrotasks();
    expect(started).toEqual([
      "profit-and-loss",
      "balance-sheet",
      "monthly-sales-purchases",
      "depreciation",
    ]);

    pending.get("balance-sheet")!([]);
    pending.get("monthly-sales-purchases")!([]);
    pending.get("depreciation")!([]);

    await expect(fsPromise).resolves.toMatchObject({
      report: "financial_statements",
      profit_and_loss: { report: "profit_and_loss" },
      balance_sheet: { report: "balance_sheet" },
      monthly: { rows: expect.any(Array) },
    });
  });

  it("tiles 月別売上・仕入 across the period and regroups 製造原価 from the PL", async () => {
    const plRows = [
      {
        code: "4110",
        name: "売上高",
        account_type: "revenue",
        statement_category: "sales",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "420000.00",
      },
      {
        code: "6110",
        name: "材料費",
        account_type: "expense",
        statement_category: "manufacturing_materials",
        normal_balance: "debit",
        debit_total: "60000.00",
        credit_total: "0.00",
      },
      {
        code: "6210",
        name: "労務費",
        account_type: "expense",
        statement_category: "manufacturing_labor",
        normal_balance: "debit",
        debit_total: "30000.00",
        credit_total: "0.00",
      },
      {
        code: "7210",
        name: "減価償却費",
        account_type: "expense",
        statement_category: "selling_admin_expenses",
        normal_balance: "debit",
        debit_total: "5000.00",
        credit_total: "0.00",
      },
    ];
    const bsRows = [
      {
        code: "1110",
        name: "現金",
        statement_category: "current_assets",
        normal_balance: "debit",
        debit_total: "500000.00",
        credit_total: "0.00",
      },
      {
        code: "3110",
        name: "元入金",
        statement_category: "equity",
        normal_balance: "credit",
        debit_total: "0.00",
        credit_total: "200000.00",
      },
    ];
    const monthlyRows = [
      {
        month: "2025-01",
        is_sales: true,
        debit_total: "0.00",
        credit_total: "100000.00",
      },
      {
        month: "2025-03",
        is_sales: true,
        debit_total: "0.00",
        credit_total: "320000.00",
      },
      {
        month: "2025-02",
        is_sales: false,
        debit_total: "50000.00",
        credit_total: "0.00",
      },
    ];
    const depreciationRows = [
      {
        code: "1610",
        name: "工具器具備品",
        normal_balance: "debit",
        debit_total: "100000.00",
        credit_total: "5000.00",
        period_credit: "5000.00",
      },
    ];

    const sql = fakeSql(plRows, bsRows, monthlyRows, depreciationRows);
    const fs = await fetchFinancialStatements(sql, {
      fiscalYear: "FY2025",
      start: "2025-01-01",
      end: "2025-03-31",
    });

    // 月別: every month in [start, end] appears, quiet months included.
    expect(fs.monthly.rows.map((r) => r.month)).toEqual([
      "2025-01",
      "2025-02",
      "2025-03",
    ]);
    expect(fs.monthly.rows[0]).toEqual({
      month: "2025-01",
      sales: "100000.00",
      purchases: "0.00",
    });
    expect(fs.monthly.rows[1]).toEqual({
      month: "2025-02",
      sales: "0.00",
      purchases: "50000.00",
    });
    expect(fs.monthly.sales_total).toBe("420000.00");
    expect(fs.monthly.purchases_total).toBe("50000.00");

    // 製造原価: regrouped from the PL本体's 売上原価 lines by 表示区分.
    expect(fs.manufacturing_cost.materials.subtotal).toBe("60000.00");
    expect(fs.manufacturing_cost.labor.subtotal).toBe("30000.00");
    expect(fs.manufacturing_cost.overhead.subtotal).toBe("0.00");
    expect(fs.manufacturing_cost.total_manufacturing_cost).toBe("90000.00");

    // 減価償却費: 当期減少額 (period credit) and the PL expense-side total to foot to.
    expect(fs.depreciation.lines).toHaveLength(1);
    expect(fs.depreciation.lines[0].depreciation_expense).toBe("5000.00");
    expect(fs.depreciation.lines[0].closing_book_value).toBe("95000.00");
    expect(fs.depreciation.total_depreciation).toBe("5000.00");
    expect(fs.depreciation.expense_total).toBe("5000.00");
  });
});

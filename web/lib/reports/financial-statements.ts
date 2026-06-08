/**
 * 青色申告決算書 (blue-return financial statements) — the TS twin of
 * `LedgerRepository.financial_statements`.
 *
 * Composes the 損益計算書 (1面) and 貸借対照表 (4面, as of 期末) verbatim, then derives the form's
 * 内訳 from the same books: 月別売上(収入)・仕入 (2面) tiles the per-month footings, 減価償却費の計算
 * (3面) reads each 固定資産's 当期減少額 (直接法), and 製造原価の計算 (4面) regroups the PL's 売上原価
 * 製造原価科目. Every breakdown is derived from the same journal/勘定科目 data — no 固定資産 / 従業員
 * マスタ — so it reconciles back to the PL/BS. Output is the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, sumMoney, ZERO, type Money } from "../money";
import { fetchBalanceSheet, type BalanceSheetSnapshot } from "./balance-sheet";
import { balanceFromTotals } from "./ledger";
import { monthLabels } from "./month";
import {
  fetchProfitAndLoss,
  type ProfitAndLossSnapshot,
  type ProfitAndLossLineSnapshot,
} from "./profit-and-loss";
import { statusFilter } from "./sql";
import {
  DEPRECIATION_ACCOUNT_NAME,
  MANUFACTURING_SECTIONS,
  PURCHASE_ACCOUNT_NAME_SUFFIX,
  type EntryStatus,
  type NormalSide,
  type StatementCategory,
} from "./types";

export interface MonthlySalesPurchasesRowSnapshot {
  month: string;
  sales: string;
  purchases: string;
}

export interface MonthlySalesPurchasesSnapshot {
  rows: MonthlySalesPurchasesRowSnapshot[];
  sales_total: string;
  purchases_total: string;
}

export interface DepreciationLineSnapshot {
  code: string;
  name: string;
  acquisition_cost: string;
  depreciation_expense: string;
  closing_book_value: string;
}

export interface DepreciationScheduleSnapshot {
  lines: DepreciationLineSnapshot[];
  total_depreciation: string;
  expense_total: string;
}

export interface ManufacturingCostLineSnapshot {
  code: string;
  name: string;
  amount: string;
}

export interface ManufacturingCostSectionSnapshot {
  key: string;
  label: string;
  lines: ManufacturingCostLineSnapshot[];
  subtotal: string;
}

export interface ManufacturingCostSnapshot {
  materials: ManufacturingCostSectionSnapshot;
  labor: ManufacturingCostSectionSnapshot;
  overhead: ManufacturingCostSectionSnapshot;
  total_manufacturing_cost: string;
  cost_of_goods_manufactured: string;
}

export interface FinancialStatementsSnapshot {
  report: "financial_statements";
  fiscal_year: string;
  start_date: string;
  end_date: string;
  profit_and_loss: ProfitAndLossSnapshot;
  monthly: MonthlySalesPurchasesSnapshot;
  depreciation: DepreciationScheduleSnapshot;
  manufacturing_cost: ManufacturingCostSnapshot;
  balance_sheet: BalanceSheetSnapshot;
}

interface MonthAmountRow {
  month: string;
  debit_total: string;
  credit_total: string;
}

type MonthAmounts = { debit: Money; credit: Money };

function byMonth(rows: MonthAmountRow[]): Map<string, MonthAmounts> {
  return new Map(
    rows.map((row) => [
      row.month,
      {
        debit: parseMoney(row.debit_total),
        credit: parseMoney(row.credit_total),
      },
    ]),
  );
}

async function monthlySalesPurchases(
  sql: Sql,
  start: string,
  end: string,
  status: EntryStatus | null,
): Promise<MonthlySalesPurchasesSnapshot> {
  const salesRows = await sql<MonthAmountRow[]>`
    SELECT to_char(date_trunc('month', je.entry_date), 'YYYY-MM') AS month,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE a.statement_category = 'sales'
      AND je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY month
  `;
  const purchaseRows = await sql<MonthAmountRow[]>`
    SELECT to_char(date_trunc('month', je.entry_date), 'YYYY-MM') AS month,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE a.name LIKE ${"%" + PURCHASE_ACCOUNT_NAME_SUFFIX}
      AND je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY month
  `;

  const salesByMonth = byMonth(salesRows);
  const purchasesByMonth = byMonth(purchaseRows);
  const rows: MonthlySalesPurchasesRowSnapshot[] = [];
  let salesTotal: Money = ZERO;
  let purchasesTotal: Money = ZERO;
  for (const label of monthLabels(start, end)) {
    const s = salesByMonth.get(label) ?? { debit: ZERO, credit: ZERO };
    const p = purchasesByMonth.get(label) ?? { debit: ZERO, credit: ZERO };
    const sales = balanceFromTotals(s.debit, s.credit, "credit"); // 収益: credit-normal
    const purchases = balanceFromTotals(p.debit, p.credit, "debit"); // 費用: debit-normal
    rows.push({
      month: label,
      sales: formatMoney(sales),
      purchases: formatMoney(purchases),
    });
    salesTotal += sales;
    purchasesTotal += purchases;
  }
  return {
    rows,
    sales_total: formatMoney(salesTotal),
    purchases_total: formatMoney(purchasesTotal),
  };
}

/** Σ of the PL's 減価償却費 lines (経費 + 製造経費) — the expense-side 償却費 to foot to. */
function depreciationExpenseTotal(pl: ProfitAndLossSnapshot): Money {
  const sections = [
    pl.cost_of_goods_sold,
    pl.selling_admin_expenses,
    pl.non_operating_expenses,
  ];
  const lines = sections.flatMap((section) => section.lines);
  return sumMoney(
    lines
      .filter((line) => line.name === DEPRECIATION_ACCOUNT_NAME)
      .map((line) => parseMoney(line.amount)),
  );
}

async function depreciationSchedule(
  sql: Sql,
  pl: ProfitAndLossSnapshot,
  start: string,
  end: string,
  status: EntryStatus | null,
): Promise<DepreciationScheduleSnapshot> {
  const rows = await sql<
    {
      code: string;
      name: string;
      normal_balance: NormalSide;
      debit_total: string;
      credit_total: string;
      period_credit: string;
    }[]
  >`
    SELECT a.code, a.name, a.normal_balance,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total,
           COALESCE(SUM(jl.amount) FILTER (
               WHERE jl.side = 'credit' AND je.entry_date >= ${start}::date), 0)::text
               AS period_credit
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE a.statement_category = 'fixed_assets'
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.name, a.normal_balance
    ORDER BY a.code
  `;

  const lines: DepreciationLineSnapshot[] = [];
  let totalDepreciation: Money = ZERO;
  for (const row of rows) {
    const depreciationExpense = parseMoney(row.period_credit);
    if (depreciationExpense === ZERO) continue; // 非償却資産 / untouched assets are left out
    const acquisitionCost = parseMoney(row.debit_total);
    const closingBookValue = balanceFromTotals(
      acquisitionCost,
      parseMoney(row.credit_total),
      row.normal_balance,
    );
    lines.push({
      code: row.code,
      name: row.name,
      acquisition_cost: formatMoney(acquisitionCost),
      depreciation_expense: formatMoney(depreciationExpense),
      closing_book_value: formatMoney(closingBookValue),
    });
    totalDepreciation += depreciationExpense;
  }

  return {
    lines,
    total_depreciation: formatMoney(totalDepreciation),
    expense_total: formatMoney(depreciationExpenseTotal(pl)),
  };
}

/** Regroup the PL本体's 製造原価科目 (folded into 売上原価) into the 製造原価の計算. */
function manufacturingCost(
  pl: ProfitAndLossSnapshot,
): ManufacturingCostSnapshot {
  const cogsLines = pl.cost_of_goods_sold.lines;
  const sectionFor = (
    category: StatementCategory,
  ): ManufacturingCostLineSnapshot[] =>
    cogsLines
      .filter((line: ProfitAndLossLineSnapshot) => line.category === category)
      .map((line) => ({
        code: line.code,
        name: line.name,
        amount: line.amount,
      }));

  const sections = new Map<string, ManufacturingCostSectionSnapshot>();
  let total: Money = ZERO;
  for (const [key, label, category] of MANUFACTURING_SECTIONS) {
    const sectionLines = sectionFor(category);
    const subtotal = sumMoney(
      sectionLines.map((line) => parseMoney(line.amount)),
    );
    sections.set(key, {
      key,
      label,
      lines: sectionLines,
      subtotal: formatMoney(subtotal),
    });
    total += subtotal;
  }

  return {
    materials: sections.get("materials")!,
    labor: sections.get("labor")!,
    overhead: sections.get("overhead")!,
    total_manufacturing_cost: formatMoney(total),
    cost_of_goods_manufactured: formatMoney(total), // 仕掛品なし
  };
}

export interface FinancialStatementsOptions {
  fiscalYear: string;
  start: string;
  end: string;
  status?: EntryStatus | null;
}

export async function fetchFinancialStatements(
  sql: Sql,
  { fiscalYear, start, end, status = "posted" }: FinancialStatementsOptions,
): Promise<FinancialStatementsSnapshot> {
  const profitAndLoss = await fetchProfitAndLoss(sql, {
    fiscalYear,
    start,
    end,
    status,
  });
  const balanceSheet = await fetchBalanceSheet(sql, { start, asOf: end, status });
  const monthly = await monthlySalesPurchases(sql, start, end, status);
  const depreciation = await depreciationSchedule(
    sql,
    profitAndLoss,
    start,
    end,
    status,
  );

  return {
    report: "financial_statements",
    fiscal_year: fiscalYear,
    start_date: start,
    end_date: end,
    profit_and_loss: profitAndLoss,
    monthly,
    depreciation,
    manufacturing_cost: manufacturingCost(profitAndLoss),
    balance_sheet: balanceSheet,
  };
}

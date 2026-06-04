/**
 * 損益計算書 (staged P/L) — the TS twin of `LedgerRepository.profit_and_loss`.
 *
 * Sums 収益 / 費用 footings over the fiscal year, signs each into a balance, buckets them by
 * 表示区分 into the 段階表示 sections, and derives 売上総利益 → 営業利益 → 経常利益 → 当期純利益 as
 * pure subtractions of the section subtotals — so the 段階利益 reconcile with the trial balance.
 * A 収益/費用 account with no P/L 区分 is collected into `unclassified` so a coverage gap is
 * visible rather than silently dropped (網羅性). Output is the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, sumMoney, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { statusFilter } from "./sql";
import {
  PL_ACCOUNT_TYPES,
  PL_CATEGORY_SECTION,
  PL_SECTIONS,
  type AccountType,
  type EntryStatus,
  type NormalSide,
  type StatementCategory,
} from "./types";

export interface ProfitAndLossLineSnapshot {
  code: string;
  name: string;
  category: StatementCategory | null;
  amount: string;
}

export interface ProfitAndLossSectionSnapshot {
  key: string;
  label: string;
  lines: ProfitAndLossLineSnapshot[];
  subtotal: string;
}

export interface ProfitAndLossSnapshot {
  report: "profit_and_loss";
  fiscal_year: string;
  start_date: string;
  end_date: string;
  sales: ProfitAndLossSectionSnapshot;
  cost_of_goods_sold: ProfitAndLossSectionSnapshot;
  gross_profit: string;
  selling_admin_expenses: ProfitAndLossSectionSnapshot;
  operating_income: string;
  non_operating_income: ProfitAndLossSectionSnapshot;
  non_operating_expenses: ProfitAndLossSectionSnapshot;
  ordinary_income: string;
  net_income: string;
  unclassified: ProfitAndLossLineSnapshot[];
}

interface PlRow {
  code: string;
  name: string;
  account_type: AccountType;
  statement_category: StatementCategory | null;
  normal_balance: NormalSide;
  debit_total: string;
  credit_total: string;
}

interface PlLine {
  line: ProfitAndLossLineSnapshot;
  amount: Money;
}

export interface ProfitAndLossOptions {
  fiscalYear: string;
  start: string;
  end: string;
  status?: EntryStatus | null;
}

export async function fetchProfitAndLoss(
  sql: Sql,
  { fiscalYear, start, end, status = "posted" }: ProfitAndLossOptions,
): Promise<ProfitAndLossSnapshot> {
  const rows = await sql<PlRow[]>`
    SELECT a.code, a.name, a.account_type, a.statement_category, a.normal_balance,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE a.account_type IN ('revenue', 'expense')
      AND je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.name, a.account_type, a.statement_category, a.normal_balance
    ORDER BY a.code
  `;

  const sectionLines = new Map<string, PlLine[]>(
    PL_SECTIONS.map(([key]) => [key, []]),
  );
  const unclassified: PlLine[] = [];
  for (const row of rows) {
    if (!PL_ACCOUNT_TYPES.has(row.account_type)) continue;
    const amount = balanceFromTotals(
      parseMoney(row.debit_total),
      parseMoney(row.credit_total),
      row.normal_balance,
    );
    const entry: PlLine = {
      amount,
      line: {
        code: row.code,
        name: row.name,
        category: row.statement_category,
        amount: formatMoney(amount),
      },
    };
    const key =
      row.statement_category !== null
        ? PL_CATEGORY_SECTION.get(row.statement_category)
        : undefined;
    if (key === undefined) {
      unclassified.push(entry);
    } else {
      sectionLines.get(key)!.push(entry);
    }
  }

  const sections = new Map<string, ProfitAndLossSectionSnapshot>();
  const subtotals = new Map<string, Money>();
  for (const [key, label] of PL_SECTIONS) {
    const entries = sectionLines.get(key)!;
    const subtotal = sumMoney(entries.map((e) => e.amount));
    subtotals.set(key, subtotal);
    sections.set(key, {
      key,
      label,
      lines: entries.map((e) => e.line),
      subtotal: formatMoney(subtotal),
    });
  }

  const sub = (key: string): Money => subtotals.get(key)!;
  const grossProfit = sub("sales") - sub("cost_of_goods_sold");
  const operatingIncome = grossProfit - sub("selling_admin_expenses");
  const ordinaryIncome =
    operatingIncome +
    sub("non_operating_income") -
    sub("non_operating_expenses");

  return {
    report: "profit_and_loss",
    fiscal_year: fiscalYear,
    start_date: start,
    end_date: end,
    sales: sections.get("sales")!,
    cost_of_goods_sold: sections.get("cost_of_goods_sold")!,
    gross_profit: formatMoney(grossProfit),
    selling_admin_expenses: sections.get("selling_admin_expenses")!,
    operating_income: formatMoney(operatingIncome),
    non_operating_income: sections.get("non_operating_income")!,
    non_operating_expenses: sections.get("non_operating_expenses")!,
    ordinary_income: formatMoney(ordinaryIncome),
    net_income: formatMoney(ordinaryIncome),
    unclassified: unclassified.map((e) => e.line),
  };
}

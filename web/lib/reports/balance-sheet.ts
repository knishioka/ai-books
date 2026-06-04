/**
 * 貸借対照表 (balance sheet) — the TS twin of `LedgerRepository.balance_sheet`.
 *
 * Sums each touched account's signed balance as of 期末 (the same shape as the trial balance,
 * carrying each account's 表示区分), then dispatches by 区分: B/S categories (資産/負債/純資産)
 * become section lines while P/L categories feed 当期純利益 (収益 add, 費用 subtract — the same
 * figure the 損益計算書 reports). 当期純利益 is folded into 純資産合計 so 資産 = 負債 + 純資産 closes.
 * A B/S account that nets to exactly zero is dropped; a touched account with no 表示区分 is a
 * chart-of-accounts data error and throws loudly (it would silently break 貸借一致). Output is
 * the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, sumMoney, ZERO, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { statusFilter } from "./sql";
import {
  ASSET_CATEGORIES,
  EQUITY_CATEGORIES,
  LIABILITY_CATEGORIES,
  STATEMENT_CATEGORY_ACCOUNT_TYPE,
  type EntryStatus,
  type NormalSide,
  type StatementCategory,
} from "./types";

export interface BalanceSheetLineSnapshot {
  code: string;
  name: string;
  balance: string;
}

export interface BalanceSheetSectionSnapshot {
  category: StatementCategory;
  lines: BalanceSheetLineSnapshot[];
  subtotal: string;
}

export interface BalanceSheetSnapshot {
  report: "balance_sheet";
  as_of: string | null;
  status: EntryStatus | null;
  assets: BalanceSheetSectionSnapshot[];
  liabilities: BalanceSheetSectionSnapshot[];
  equity: BalanceSheetSectionSnapshot[];
  net_income: string;
  total_assets: string;
  total_liabilities: string;
  total_equity: string;
}

interface BalanceRow {
  code: string;
  name: string;
  statement_category: StatementCategory | null;
  normal_balance: NormalSide;
  debit_total: string;
  credit_total: string;
}

interface ClassifiedLine {
  line: BalanceSheetLineSnapshot;
  balance: Money;
}

export interface BalanceSheetOptions {
  /** Inclusive upper bound on 取引日 (`null` = cumulative all-time). */
  asOf?: string | null;
  status?: EntryStatus | null;
}

function buildSections(
  byCategory: Map<StatementCategory, ClassifiedLine[]>,
  categories: ReadonlyArray<StatementCategory>,
): { sections: BalanceSheetSectionSnapshot[]; total: Money } {
  const sections: BalanceSheetSectionSnapshot[] = [];
  let total: Money = ZERO;
  for (const category of categories) {
    const entries = byCategory.get(category) ?? [];
    const subtotal = sumMoney(entries.map((e) => e.balance));
    sections.push({
      category,
      lines: entries.map((e) => e.line),
      subtotal: formatMoney(subtotal),
    });
    total += subtotal;
  }
  return { sections, total };
}

export async function fetchBalanceSheet(
  sql: Sql,
  { asOf = null, status = "posted" }: BalanceSheetOptions = {},
): Promise<BalanceSheetSnapshot> {
  const rows = await sql<BalanceRow[]>`
    SELECT a.code, a.name, a.statement_category, a.normal_balance,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE (${asOf}::date IS NULL OR je.entry_date <= ${asOf}::date)
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.name, a.statement_category, a.normal_balance
    ORDER BY a.code
  `;

  const byCategory = new Map<StatementCategory, ClassifiedLine[]>();
  let netIncome: Money = ZERO;
  for (const row of rows) {
    if (row.statement_category === null) {
      throw new Error(
        `account ${row.code} (${row.name}) has no statement_category; cannot assemble balance sheet`,
      );
    }
    const balance = balanceFromTotals(
      parseMoney(row.debit_total),
      parseMoney(row.credit_total),
      row.normal_balance,
    );
    const accountType = STATEMENT_CATEGORY_ACCOUNT_TYPE[row.statement_category];
    if (accountType === "revenue") {
      netIncome += balance;
    } else if (accountType === "expense") {
      netIncome -= balance;
    } else if (balance !== ZERO) {
      const list = byCategory.get(row.statement_category) ?? [];
      list.push({
        balance,
        line: { code: row.code, name: row.name, balance: formatMoney(balance) },
      });
      byCategory.set(row.statement_category, list);
    }
  }

  const assets = buildSections(byCategory, ASSET_CATEGORIES);
  const liabilities = buildSections(byCategory, LIABILITY_CATEGORIES);
  const equity = buildSections(byCategory, EQUITY_CATEGORIES);

  return {
    report: "balance_sheet",
    as_of: asOf,
    status,
    assets: assets.sections,
    liabilities: liabilities.sections,
    equity: equity.sections,
    net_income: formatMoney(netIncome),
    total_assets: formatMoney(assets.total),
    total_liabilities: formatMoney(liabilities.total),
    total_equity: formatMoney(equity.total + netIncome),
  };
}

/**
 * 合計残高試算表 (trial balance) — the TS twin of `LedgerRepository.trial_balance`.
 *
 * One GROUP BY sums 借方 / 貸方 per account over the window; {@link balanceFromTotals} signs each
 * into a balance the one way the codebase signs balances, and the column footings are the plain
 * sums (借方合計 = 貸方合計 holds exactly when the books balance). The returned object is the
 * golden snapshot shape verbatim, so the viewer renders — and the golden harness checks — the
 * same structure.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, ZERO, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { statusFilter } from "./sql";
import type { EntryStatus, NormalSide } from "./types";

export interface TrialBalanceRowSnapshot {
  code: string;
  name: string;
  debit_total: string;
  credit_total: string;
  balance: string;
}

export interface TrialBalanceSnapshot {
  report: "trial_balance";
  fiscal_year: string;
  rows: TrialBalanceRowSnapshot[];
  total_debit: string;
  total_credit: string;
}

interface TotalsRow {
  code: string;
  name: string;
  normal_balance: NormalSide;
  debit_total: string;
  credit_total: string;
}

export interface TrialBalanceOptions {
  fiscalYear: string;
  /** Inclusive lower bound on 取引日 (`null` = open). */
  start?: string | null;
  /** Inclusive upper bound on 取引日 (`null` = cumulative all-time). */
  asOf?: string | null;
  status?: EntryStatus | null;
}

export async function fetchTrialBalance(
  sql: Sql,
  {
    fiscalYear,
    start = null,
    asOf = null,
    status = "posted",
  }: TrialBalanceOptions,
): Promise<TrialBalanceSnapshot> {
  const totals = await sql<TotalsRow[]>`
    SELECT a.code, a.name, a.normal_balance,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE (${start}::date IS NULL OR je.entry_date >= ${start}::date)
      AND (${asOf}::date IS NULL OR je.entry_date <= ${asOf}::date)
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.name, a.normal_balance
    ORDER BY a.code
  `;

  const rows: TrialBalanceRowSnapshot[] = [];
  let totalDebit: Money = ZERO;
  let totalCredit: Money = ZERO;
  for (const row of totals) {
    const debit = parseMoney(row.debit_total);
    const credit = parseMoney(row.credit_total);
    const balance = balanceFromTotals(debit, credit, row.normal_balance);
    rows.push({
      code: row.code,
      name: row.name,
      debit_total: formatMoney(debit),
      credit_total: formatMoney(credit),
      balance: formatMoney(balance),
    });
    totalDebit += debit;
    totalCredit += credit;
  }

  return {
    report: "trial_balance",
    fiscal_year: fiscalYear,
    rows,
    total_debit: formatMoney(totalDebit),
    total_credit: formatMoney(totalCredit),
  };
}

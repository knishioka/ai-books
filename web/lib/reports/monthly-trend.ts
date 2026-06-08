/**
 * 月次推移 (monthly trend) — the TS twin of `LedgerRepository.monthly_trend`.
 *
 * The opening balance is everything posted *before* 期首 (期首残高); the fiscal year is tiled
 * into accounting months ({@link monthLabels}) and each month's 借方 / 貸方 sums roll the running
 * balance forward, so a quiet month still appears with the balance unchanged (月次推移が会計期間
 * で正しく区切られる). By construction 期首残高 + Σ期中増減 = 期末残高. Output is the golden
 * snapshot shape verbatim (`account_id` is deliberately omitted — it is DB-assigned).
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, ZERO, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { monthLabels } from "./month";
import { statusFilter } from "./sql";
import type { EntryStatus, NormalSide } from "./types";

export interface MonthlyTrendPointSnapshot {
  month: string;
  debit_total: string;
  credit_total: string;
  net_change: string;
  closing_balance: string;
}

export interface MonthlyTrendAccountSnapshot {
  code: string;
  name: string;
  normal_balance: NormalSide;
  opening_balance: string;
  closing_balance: string;
  points: MonthlyTrendPointSnapshot[];
}

export interface MonthlyTrendSnapshot {
  report: "monthly_trend";
  fiscal_year: string;
  accounts: MonthlyTrendAccountSnapshot[];
}

interface AccountMeta {
  id: string;
  code: string;
  name: string;
  normal_balance: NormalSide;
}

export interface MonthlyTrendOptions {
  /** Accounts to chart, by 勘定科目コード, in display order. */
  codes: string[];
  fiscalYear: string;
  start: string;
  end: string;
  status?: EntryStatus | null;
  carryForward?: boolean;
}

async function trendForAccount(
  sql: Sql,
  account: AccountMeta,
  start: string,
  end: string,
  status: EntryStatus | null,
  carryForward: boolean,
): Promise<MonthlyTrendAccountSnapshot> {
  let opening = ZERO;
  if (carryForward) {
    const [open] = await sql<{ debit_total: string; credit_total: string }[]>`
      SELECT COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
             COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
      FROM journal_lines jl
      JOIN journal_entries je ON je.id = jl.entry_id
      WHERE jl.account_id = ${account.id}::bigint
        AND je.entry_date < ${start}::date
        AND ${statusFilter(sql, status)}
    `;
    opening = balanceFromTotals(
      parseMoney(open.debit_total),
      parseMoney(open.credit_total),
      account.normal_balance,
    );
  }

  const monthRows = await sql<
    { month: string; debit_total: string; credit_total: string }[]
  >`
    SELECT to_char(date_trunc('month', je.entry_date), 'YYYY-MM') AS month,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    WHERE jl.account_id = ${account.id}::bigint
      AND je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY month
  `;
  const byMonth = new Map(monthRows.map((row) => [row.month, row]));

  let running = opening;
  const points: MonthlyTrendPointSnapshot[] = monthLabels(start, end).map(
    (label) => {
      const amounts = byMonth.get(label);
      const debit = amounts ? parseMoney(amounts.debit_total) : ZERO;
      const credit = amounts ? parseMoney(amounts.credit_total) : ZERO;
      const netChange = balanceFromTotals(
        debit,
        credit,
        account.normal_balance,
      );
      running += netChange;
      return {
        month: label,
        debit_total: formatMoney(debit),
        credit_total: formatMoney(credit),
        net_change: formatMoney(netChange),
        closing_balance: formatMoney(running),
      };
    },
  );

  return {
    code: account.code,
    name: account.name,
    normal_balance: account.normal_balance,
    opening_balance: formatMoney(opening),
    closing_balance: formatMoney(running),
    points,
  };
}

export async function fetchMonthlyTrend(
  sql: Sql,
  {
    codes,
    fiscalYear,
    start,
    end,
    status = "posted",
    carryForward = true,
  }: MonthlyTrendOptions,
): Promise<MonthlyTrendSnapshot> {
  const accounts: MonthlyTrendAccountSnapshot[] = [];
  for (const code of codes) {
    const [account] = await sql<AccountMeta[]>`
      SELECT id::text AS id, code, name, normal_balance
      FROM accounts
      WHERE code = ${code}
    `;
    if (!account) throw new Error(`account ${code} not found`);
    accounts.push(
      await trendForAccount(sql, account, start, end, status, carryForward),
    );
  }
  return { report: "monthly_trend", fiscal_year: fiscalYear, accounts };
}

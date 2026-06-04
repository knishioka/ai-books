/**
 * 総勘定元帳 (general ledger) — the TS twin of `LedgerRepository.general_ledger`.
 *
 * The whole book (every account touched on/before 期末, 科目コード順) or one account, each with
 * its 繰越 (opening) and 期末残高 (closing) and a per-row running balance. The opening balance is
 * everything *before* `start` (繰越); each in-window line moves the running balance in the
 * account's normal direction (via {@link signedDelta}); `counter_accounts` are the other accounts
 * in the same 伝票 (相手科目). Output is the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, ZERO, type Money } from "../money";
import { balanceFromTotals, signedDelta } from "./ledger";
import { statusFilter } from "./sql";
import type { EntrySide, EntryStatus, NormalSide } from "./types";

export interface GeneralLedgerRowSnapshot {
  entry_date: string;
  voucher_no: string | null;
  description: string | null;
  line_description: string | null;
  counter_accounts: string[];
  side: EntrySide;
  amount: string;
  running_balance: string;
}

export interface GeneralLedgerAccountSnapshot {
  code: string;
  name: string;
  normal_balance: NormalSide;
  opening_balance: string;
  closing_balance: string;
  rows: GeneralLedgerRowSnapshot[];
}

export interface GeneralLedgerSnapshot {
  report: "general_ledger";
  start_date: string | null;
  end_date: string | null;
  status: EntryStatus | null;
  accounts: GeneralLedgerAccountSnapshot[];
}

interface AccountMeta {
  id: string;
  code: string;
  name: string;
  normal_balance: NormalSide;
}

interface LedgerLineRow {
  entry_id: string;
  line_no: number;
  entry_date: string;
  voucher_no: string | null;
  description: string | null;
  line_description: string | null;
  side: EntrySide;
  amount: string;
}

export interface GeneralLedgerOptions {
  /** Limit to one account by 勘定科目コード; omit for the whole book. */
  accountCode?: string | null;
  start?: string | null;
  end?: string | null;
  status?: EntryStatus | null;
}

async function openingBalance(
  sql: Sql,
  accountId: string,
  start: string | null,
  normal: NormalSide,
  status: EntryStatus | null,
): Promise<Money> {
  // With no start there is no 繰越 — opening is zero and the rows cover all history.
  if (start === null) return ZERO;
  const [row] = await sql<{ debit_total: string; credit_total: string }[]>`
    SELECT COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    WHERE jl.account_id = ${accountId}::bigint
      AND je.entry_date < ${start}::date
      AND ${statusFilter(sql, status)}
  `;
  return balanceFromTotals(
    parseMoney(row.debit_total),
    parseMoney(row.credit_total),
    normal,
  );
}

async function accountLedger(
  sql: Sql,
  account: AccountMeta,
  {
    start,
    end,
    status,
  }: { start: string | null; end: string | null; status: EntryStatus | null },
): Promise<GeneralLedgerAccountSnapshot> {
  const opening = await openingBalance(
    sql,
    account.id,
    start,
    account.normal_balance,
    status,
  );

  const lineRows = await sql<LedgerLineRow[]>`
    SELECT je.id::text AS entry_id, jl.line_no, je.entry_date::text AS entry_date, je.voucher_no,
           je.description, jl.line_description, jl.side, jl.amount::text AS amount
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    WHERE jl.account_id = ${account.id}::bigint
      AND (${start}::date IS NULL OR je.entry_date >= ${start}::date)
      AND (${end}::date IS NULL OR je.entry_date <= ${end}::date)
      AND ${statusFilter(sql, status)}
    ORDER BY je.entry_date, je.id, jl.line_no
  `;

  const entryIds = [...new Set(lineRows.map((r) => r.entry_id))];
  const counterByEntry = new Map<string, string[]>();
  if (entryIds.length > 0) {
    const counters = await sql<{ entry_id: string; code: string }[]>`
      SELECT jl.entry_id::text AS entry_id, a.code
      FROM journal_lines jl
      JOIN accounts a ON a.id = jl.account_id
      WHERE jl.entry_id = ANY(${entryIds}::bigint[]) AND jl.account_id <> ${account.id}::bigint
      ORDER BY jl.entry_id, jl.line_no
    `;
    for (const row of counters) {
      const codes = counterByEntry.get(row.entry_id) ?? [];
      if (!codes.includes(row.code)) codes.push(row.code); // collapse dup counter accounts, keep order
      counterByEntry.set(row.entry_id, codes);
    }
  }

  let running = opening;
  const rows: GeneralLedgerRowSnapshot[] = lineRows.map((row) => {
    running += signedDelta(
      row.side,
      account.normal_balance,
      parseMoney(row.amount),
    );
    return {
      entry_date: row.entry_date,
      voucher_no: row.voucher_no,
      description: row.description,
      line_description: row.line_description,
      counter_accounts: counterByEntry.get(row.entry_id) ?? [],
      side: row.side,
      amount: formatMoney(parseMoney(row.amount)),
      running_balance: formatMoney(running),
    };
  });

  return {
    code: account.code,
    name: account.name,
    normal_balance: account.normal_balance,
    opening_balance: formatMoney(opening),
    closing_balance: formatMoney(running),
    rows,
  };
}

export async function fetchGeneralLedger(
  sql: Sql,
  {
    accountCode = null,
    start = null,
    end = null,
    status = "posted",
  }: GeneralLedgerOptions = {},
): Promise<GeneralLedgerSnapshot> {
  let accounts: AccountMeta[];
  if (accountCode !== null) {
    accounts = await sql<AccountMeta[]>`
      SELECT id::text AS id, code, name, normal_balance
      FROM accounts
      WHERE code = ${accountCode}
    `;
    if (accounts.length === 0) {
      throw new Error(`account ${accountCode} not found`);
    }
  } else {
    // "Active" = any line dated on/before 期末 under the status filter (科目コード順) — so an
    // account touched only before `start` still appears with its 繰越 and no in-window rows.
    accounts = await sql<AccountMeta[]>`
      SELECT a.id::text AS id, a.code, a.name, a.normal_balance
      FROM accounts a
      WHERE EXISTS (
        SELECT 1
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_id = a.id
          AND (${end}::date IS NULL OR je.entry_date <= ${end}::date)
          AND ${statusFilter(sql, status)}
      )
      ORDER BY a.code
    `;
  }

  const accountSnapshots: GeneralLedgerAccountSnapshot[] = [];
  for (const account of accounts) {
    accountSnapshots.push(
      await accountLedger(sql, account, { start, end, status }),
    );
  }

  return {
    report: "general_ledger",
    start_date: start,
    end_date: end,
    status,
    accounts: accountSnapshots,
  };
}

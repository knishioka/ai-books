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

interface WholeBookLedgerLineRow extends LedgerLineRow {
  account_id: string;
}

interface OpeningFootingRow {
  account_id: string;
  debit_total: string;
  credit_total: string;
}

interface EntryLineRow {
  entry_id: string;
  account_id: string;
  code: string;
}

export interface GeneralLedgerOptions {
  /** Limit to one account by 勘定科目コード; omit for the whole book. */
  accountCode?: string | null;
  start?: string | null;
  end?: string | null;
  status?: EntryStatus | null;
  carryForward?: boolean;
}

async function openingBalance(
  sql: Sql,
  accountId: string,
  start: string | null,
  normal: NormalSide,
  status: EntryStatus | null,
  carryForward: boolean,
): Promise<Money> {
  if (!carryForward || start === null) return ZERO;
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

function buildAccountLedger(
  account: AccountMeta,
  opening: Money,
  lineRows: LedgerLineRow[],
  counterAccounts: (row: LedgerLineRow) => string[],
): GeneralLedgerAccountSnapshot {
  let running = opening;
  const rows: GeneralLedgerRowSnapshot[] = lineRows.map((row) => {
    const amount = parseMoney(row.amount);
    running += signedDelta(row.side, account.normal_balance, amount);
    return {
      entry_date: row.entry_date,
      voucher_no: row.voucher_no,
      description: row.description,
      line_description: row.line_description,
      counter_accounts: counterAccounts(row),
      side: row.side,
      amount: formatMoney(amount),
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

async function accountLedger(
  sql: Sql,
  account: AccountMeta,
  {
    start,
    end,
    status,
    carryForward,
  }: {
    start: string | null;
    end: string | null;
    status: EntryStatus | null;
    carryForward: boolean;
  },
): Promise<GeneralLedgerAccountSnapshot> {
  const opening = await openingBalance(
    sql,
    account.id,
    start,
    account.normal_balance,
    status,
    carryForward,
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

  return buildAccountLedger(
    account,
    opening,
    lineRows,
    (row) => counterByEntry.get(row.entry_id) ?? [],
  );
}

function groupByAccount<T extends { account_id: string }>(
  rows: T[],
): Map<string, T[]> {
  const grouped = new Map<string, T[]>();
  for (const row of rows) {
    const accountRows = grouped.get(row.account_id);
    if (accountRows === undefined) {
      grouped.set(row.account_id, [row]);
    } else {
      accountRows.push(row);
    }
  }
  return grouped;
}

function counterFor(entryLines: EntryLineRow[], accountId: string): string[] {
  const codes: string[] = [];
  for (const line of entryLines) {
    if (line.account_id !== accountId && !codes.includes(line.code)) {
      codes.push(line.code);
    }
  }
  return codes;
}

async function wholeBookAccounts(
  sql: Sql,
  {
    start,
    end,
    status,
    carryForward,
  }: {
    start: string | null;
    end: string | null;
    status: EntryStatus | null;
    carryForward: boolean;
  },
): Promise<GeneralLedgerAccountSnapshot[]> {
  const accounts =
    carryForward && start !== null
      ? await sql<AccountMeta[]>`
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
        `
      : await sql<AccountMeta[]>`
          SELECT a.id::text AS id, a.code, a.name, a.normal_balance
          FROM accounts a
          WHERE EXISTS (
            SELECT 1
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            WHERE jl.account_id = a.id
              AND (${start}::date IS NULL OR je.entry_date >= ${start}::date)
              AND (${end}::date IS NULL OR je.entry_date <= ${end}::date)
              AND ${statusFilter(sql, status)}
          )
          ORDER BY a.code
        `;
  if (accounts.length === 0) return [];

  const openingRows =
    carryForward && start !== null
      ? await sql<OpeningFootingRow[]>`
          SELECT jl.account_id::text AS account_id,
                 COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
                 COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
          FROM journal_lines jl
          JOIN journal_entries je ON je.id = jl.entry_id
          WHERE je.entry_date < ${start}::date
            AND ${statusFilter(sql, status)}
          GROUP BY jl.account_id
        `
      : [];
  const openingByAccount = new Map(openingRows.map((row) => [row.account_id, row]));

  const lineRows = await sql<WholeBookLedgerLineRow[]>`
    SELECT jl.account_id::text AS account_id, je.id::text AS entry_id, jl.line_no,
           je.entry_date::text AS entry_date, je.voucher_no, je.description,
           jl.line_description, jl.side, jl.amount::text AS amount
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    WHERE (${start}::date IS NULL OR je.entry_date >= ${start}::date)
      AND (${end}::date IS NULL OR je.entry_date <= ${end}::date)
      AND ${statusFilter(sql, status)}
    ORDER BY jl.account_id, je.entry_date, je.id, jl.line_no
  `;
  const linesByAccount = groupByAccount(lineRows);

  const entryIdSet = new Set<string>();
  for (const row of lineRows) {
    entryIdSet.add(row.entry_id);
  }
  const entryIds = [...entryIdSet];
  const entryLinesByEntry = new Map<string, EntryLineRow[]>();
  if (entryIds.length > 0) {
    const entryLines = await sql<EntryLineRow[]>`
      SELECT jl.entry_id::text AS entry_id, jl.account_id::text AS account_id, a.code
      FROM journal_lines jl
      JOIN accounts a ON a.id = jl.account_id
      WHERE jl.entry_id = ANY(${entryIds}::bigint[])
      ORDER BY jl.entry_id, jl.line_no
    `;
    for (const row of entryLines) {
      const lines = entryLinesByEntry.get(row.entry_id) ?? [];
      lines.push(row);
      entryLinesByEntry.set(row.entry_id, lines);
    }
  }

  return accounts.map((account) => {
    const openingFooting = openingByAccount.get(account.id);
    const opening =
      openingFooting === undefined
        ? ZERO
        : balanceFromTotals(
            parseMoney(openingFooting.debit_total),
            parseMoney(openingFooting.credit_total),
            account.normal_balance,
          );

    return buildAccountLedger(
      account,
      opening,
      linesByAccount.get(account.id) ?? [],
      (row) => counterFor(entryLinesByEntry.get(row.entry_id) ?? [], account.id),
    );
  });
}

async function selectAccountByCode(
  sql: Sql,
  accountCode: string,
): Promise<AccountMeta> {
  const accounts = await sql<AccountMeta[]>`
    SELECT id::text AS id, code, name, normal_balance
    FROM accounts
    WHERE code = ${accountCode}
  `;
  const account = accounts[0];
  if (account === undefined) {
    throw new Error(`account ${accountCode} not found`);
  }
  return account;
}

export async function fetchGeneralLedger(
  sql: Sql,
  {
    accountCode = null,
    start = null,
    end = null,
    status = "posted",
    carryForward = true,
  }: GeneralLedgerOptions = {},
): Promise<GeneralLedgerSnapshot> {
  let accounts: GeneralLedgerAccountSnapshot[];
  if (accountCode !== null) {
    const account = await selectAccountByCode(sql, accountCode);
    accounts = [
      await accountLedger(sql, account, { start, end, status, carryForward }),
    ];
  } else {
    accounts = await wholeBookAccounts(sql, { start, end, status, carryForward });
  }

  return {
    report: "general_ledger",
    start_date: start,
    end_date: end,
    status,
    accounts,
  };
}

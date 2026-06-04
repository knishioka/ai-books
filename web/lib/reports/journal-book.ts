/**
 * 仕訳帳 (journal book) — the TS twin of `JournalRepository.journal_book`.
 *
 * The chronological 保存義務帳簿: every 伝票 over the period in 取引日 → 伝票番号 order, oldest
 * first, each line naming its 勘定科目 inline so the book is self-contained. `total_debit` /
 * `total_credit` foot the listed lines (借貸平均). Output is the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, ZERO, type Money } from "../money";
import { statusFilter } from "./sql";
import type { EntrySide, EntryStatus } from "./types";

export interface JournalBookLineSnapshot {
  account_code: string;
  account_name: string;
  side: EntrySide;
  amount: string;
  line_description: string | null;
}

export interface JournalBookEntrySnapshot {
  voucher_no: string | null;
  entry_date: string;
  description: string | null;
  status: EntryStatus;
  void_reason: string | null;
  lines: JournalBookLineSnapshot[];
}

export interface JournalBookSnapshot {
  report: "journal_book";
  start_date: string | null;
  end_date: string | null;
  status: EntryStatus | null;
  entries: JournalBookEntrySnapshot[];
  total_debit: string;
  total_credit: string;
}

interface HeaderRow {
  id: string;
  entry_date: string;
  voucher_no: string | null;
  description: string | null;
  status: EntryStatus;
  void_reason: string | null;
}

interface LineRow {
  entry_id: string;
  code: string;
  name: string;
  side: EntrySide;
  amount: string;
  line_description: string | null;
}

export interface JournalBookOptions {
  start?: string | null;
  end?: string | null;
  status?: EntryStatus | null;
}

export async function fetchJournalBook(
  sql: Sql,
  { start = null, end = null, status = "posted" }: JournalBookOptions = {},
): Promise<JournalBookSnapshot> {
  const headers = await sql<HeaderRow[]>`
    SELECT je.id::text AS id, je.entry_date::text AS entry_date, je.voucher_no, je.description,
           je.status, je.void_reason
    FROM journal_entries je
    WHERE (${start}::date IS NULL OR je.entry_date >= ${start}::date)
      AND (${end}::date IS NULL OR je.entry_date <= ${end}::date)
      AND ${statusFilter(sql, status)}
    ORDER BY je.entry_date, je.voucher_no NULLS LAST, je.id
  `;

  const entryIds = headers.map((h) => h.id);
  const linesByEntry = new Map<string, JournalBookLineSnapshot[]>();
  if (entryIds.length > 0) {
    const lineRows = await sql<LineRow[]>`
      SELECT jl.entry_id::text AS entry_id, a.code, a.name, jl.side,
             jl.amount::text AS amount, jl.line_description
      FROM journal_lines jl
      JOIN accounts a ON a.id = jl.account_id
      WHERE jl.entry_id = ANY(${entryIds}::bigint[])
      ORDER BY jl.entry_id, jl.line_no
    `;
    for (const row of lineRows) {
      const list = linesByEntry.get(row.entry_id) ?? [];
      list.push({
        account_code: row.code,
        account_name: row.name,
        side: row.side,
        amount: formatMoney(parseMoney(row.amount)),
        line_description: row.line_description,
      });
      linesByEntry.set(row.entry_id, list);
    }
  }

  const entries: JournalBookEntrySnapshot[] = [];
  let totalDebit: Money = ZERO;
  let totalCredit: Money = ZERO;
  for (const header of headers) {
    const lines = linesByEntry.get(header.id) ?? [];
    for (const line of lines) {
      const amount = parseMoney(line.amount);
      if (line.side === "debit") totalDebit += amount;
      else totalCredit += amount;
    }
    entries.push({
      voucher_no: header.voucher_no,
      entry_date: header.entry_date,
      description: header.description,
      status: header.status,
      void_reason: header.void_reason,
      lines,
    });
  }

  return {
    report: "journal_book",
    start_date: start,
    end_date: end,
    status,
    entries,
    total_debit: formatMoney(totalDebit),
    total_credit: formatMoney(totalCredit),
  };
}

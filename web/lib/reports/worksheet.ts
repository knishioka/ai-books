/**
 * 精算表 (worksheet) — the TS twin of `LedgerRepository.worksheet`.
 *
 * One GROUP BY splits every touched account's 借方 / 貸方 footings by whether the entry's
 * `source` marks it a 期末整理仕訳 (the 修正記入 columns) or an operating entry (the 残高試算表
 * columns). Per account: the unadjusted net fills 残高試算表, the adjustment footings fill 修正
 * 記入 gross, and the *adjusted* net is routed to the 損益計算書欄 (収益/費用) or 貸借対照表欄
 * (資産/負債/純資産) on the side its sign falls on. 当期純利益 = 収益計 − 費用計 ties the PL 欄 and
 * BS 欄 when the books balance. Output is the golden snapshot shape verbatim.
 */

import type { Sql } from "postgres";

import { formatMoney, parseMoney, ZERO, type Money } from "../money";
import { statusFilter } from "./sql";
import {
  PL_ACCOUNT_TYPES,
  YEAR_END_ADJUSTMENT_SOURCE,
  type AccountType,
  type EntryStatus,
} from "./types";

export interface WorksheetRowSnapshot {
  code: string;
  name: string;
  account_type: AccountType;
  trial_debit: string;
  trial_credit: string;
  adjustment_debit: string;
  adjustment_credit: string;
  pl_debit: string;
  pl_credit: string;
  bs_debit: string;
  bs_credit: string;
}

export interface WorksheetSnapshot {
  report: "worksheet";
  fiscal_year: string;
  rows: WorksheetRowSnapshot[];
  trial_debit_total: string;
  trial_credit_total: string;
  adjustment_debit_total: string;
  adjustment_credit_total: string;
  pl_debit_total: string;
  pl_credit_total: string;
  bs_debit_total: string;
  bs_credit_total: string;
  net_income: string;
}

interface WorksheetRow {
  code: string;
  name: string;
  account_type: AccountType;
  unadjusted_debit: string;
  unadjusted_credit: string;
  adjustment_debit: string;
  adjustment_credit: string;
}

/** Place a debit-positive net on a (借方, 貸方) column-pair (mirrors `_split_net`). */
function splitNet(net: Money): [Money, Money] {
  if (net > ZERO) return [net, ZERO];
  if (net < ZERO) return [ZERO, -net];
  return [ZERO, ZERO];
}

export interface WorksheetOptions {
  fiscalYear: string;
  start: string;
  end: string;
  status?: EntryStatus | null;
}

export async function fetchWorksheet(
  sql: Sql,
  { fiscalYear, start, end, status = "posted" }: WorksheetOptions,
): Promise<WorksheetSnapshot> {
  const adjustment = YEAR_END_ADJUSTMENT_SOURCE;
  const rows = await sql<WorksheetRow[]>`
    SELECT a.code, a.name, a.account_type,
           COALESCE(SUM(jl.amount) FILTER (
               WHERE jl.side = 'debit' AND je.source <> ${adjustment}), 0)::text
               AS unadjusted_debit,
           COALESCE(SUM(jl.amount) FILTER (
               WHERE jl.side = 'credit' AND je.source <> ${adjustment}), 0)::text
               AS unadjusted_credit,
           COALESCE(SUM(jl.amount) FILTER (
               WHERE jl.side = 'debit' AND je.source = ${adjustment}), 0)::text
               AS adjustment_debit,
           COALESCE(SUM(jl.amount) FILTER (
               WHERE jl.side = 'credit' AND je.source = ${adjustment}), 0)::text
               AS adjustment_credit
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.name, a.account_type
    ORDER BY a.code
  `;

  const out: WorksheetRowSnapshot[] = [];
  let trialDebitTotal: Money = ZERO;
  let trialCreditTotal: Money = ZERO;
  let adjustmentDebitTotal: Money = ZERO;
  let adjustmentCreditTotal: Money = ZERO;
  let plDebitTotal: Money = ZERO;
  let plCreditTotal: Money = ZERO;
  let bsDebitTotal: Money = ZERO;
  let bsCreditTotal: Money = ZERO;

  for (const row of rows) {
    const unadjustedDebit = parseMoney(row.unadjusted_debit);
    const unadjustedCredit = parseMoney(row.unadjusted_credit);
    const adjustmentDebit = parseMoney(row.adjustment_debit);
    const adjustmentCredit = parseMoney(row.adjustment_credit);

    const unadjustedNet = unadjustedDebit - unadjustedCredit;
    const [trialDebit, trialCredit] = splitNet(unadjustedNet);
    const adjustedNet = unadjustedNet + adjustmentDebit - adjustmentCredit;
    const [colDebit, colCredit] = splitNet(adjustedNet);
    const isPl = PL_ACCOUNT_TYPES.has(row.account_type);
    const plDebit = isPl ? colDebit : ZERO;
    const plCredit = isPl ? colCredit : ZERO;
    const bsDebit = isPl ? ZERO : colDebit;
    const bsCredit = isPl ? ZERO : colCredit;

    out.push({
      code: row.code,
      name: row.name,
      account_type: row.account_type,
      trial_debit: formatMoney(trialDebit),
      trial_credit: formatMoney(trialCredit),
      adjustment_debit: formatMoney(adjustmentDebit),
      adjustment_credit: formatMoney(adjustmentCredit),
      pl_debit: formatMoney(plDebit),
      pl_credit: formatMoney(plCredit),
      bs_debit: formatMoney(bsDebit),
      bs_credit: formatMoney(bsCredit),
    });

    trialDebitTotal += trialDebit;
    trialCreditTotal += trialCredit;
    adjustmentDebitTotal += adjustmentDebit;
    adjustmentCreditTotal += adjustmentCredit;
    plDebitTotal += plDebit;
    plCreditTotal += plCredit;
    bsDebitTotal += bsDebit;
    bsCreditTotal += bsCredit;
  }

  return {
    report: "worksheet",
    fiscal_year: fiscalYear,
    rows: out,
    trial_debit_total: formatMoney(trialDebitTotal),
    trial_credit_total: formatMoney(trialCreditTotal),
    adjustment_debit_total: formatMoney(adjustmentDebitTotal),
    adjustment_credit_total: formatMoney(adjustmentCreditTotal),
    pl_debit_total: formatMoney(plDebitTotal),
    pl_credit_total: formatMoney(plCreditTotal),
    bs_debit_total: formatMoney(bsDebitTotal),
    bs_credit_total: formatMoney(bsCreditTotal),
    net_income: formatMoney(plCreditTotal - plDebitTotal),
  };
}

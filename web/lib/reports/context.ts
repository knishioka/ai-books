/**
 * Report-page loader: resolve the fiscal year to display and build a report inside one DB round.
 *
 * Every report page needs the same preamble — open the read-only client, resolve which 会計年度
 * to show (from `?fy=`, defaulting to the newest seeded year), and list the years for the period
 * selector — before it builds its specific report. {@link loadReport} centralizes that so each
 * page is just its `build` callback, and degrades to the shared "未接続 / 未シード" banner instead
 * of throwing.
 */

import "server-only";

import type { Sql } from "postgres";

import { tryQuery, type ConnectionResult } from "../db";
import { resolveFiscalYear, type FiscalYear } from "./fiscal-year";

export interface ReportContext<T> {
  fiscalYear: FiscalYear;
  fiscalYears: FiscalYear[];
  data: T;
}

/**
 * Resolve the fiscal year (honoring `requestedYear`, else newest) and run `build` against it,
 * wrapping the result in a {@link ConnectionResult}. Fails with a friendly error when no database
 * is configured or no 会計年度 is seeded yet.
 */
export function loadReport<T>(
  requestedYear: string | undefined,
  build: (sql: Sql, fiscalYear: FiscalYear) => Promise<T>,
): Promise<ConnectionResult<ReportContext<T>>> {
  return tryQuery(async (sql) => {
    const fiscalYears = await sql<FiscalYear[]>`
      SELECT name, start_date::text AS start_date, end_date::text AS end_date
      FROM fiscal_years
      ORDER BY start_date DESC
    `;
    const fiscalYear = await resolveFiscalYear(sql, requestedYear);
    if (!fiscalYear) {
      throw new Error(
        "会計年度がまだ登録されていません。MCP 経由で仕訳を登録し、fiscal_years をシードしてください。",
      );
    }
    const data = await build(sql, fiscalYear);
    return { fiscalYear, fiscalYears, data };
  });
}

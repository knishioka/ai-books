/**
 * Fiscal-year resolution for the viewer's period selector.
 *
 * Reports are scoped to a 会計年度 (期首 / 期末). The viewer lists the seeded `fiscal_years` rows
 * so a reader can switch year; cumulative reports (試算表 / 貸借対照表) read *as of* 期末, period
 * reports (PL / 精算表 / 月次推移 / 決算書) read over `[期首, 期末]` — the same boundaries the
 * Python repository resolves a fiscal year to.
 */

import "server-only";

import type { Sql } from "postgres";

import { tryQuery, type ConnectionResult } from "../db";

export interface FiscalYear {
  name: string;
  /** 期首 (ISO `YYYY-MM-DD`). */
  start_date: string;
  /** 期末 (ISO `YYYY-MM-DD`). */
  end_date: string;
}

/** List every seeded fiscal year, newest first (the selector's options). */
export function fetchFiscalYears(): Promise<ConnectionResult<FiscalYear[]>> {
  return tryQuery(
    (sql) => sql<FiscalYear[]>`
      SELECT name, start_date::text AS start_date, end_date::text AS end_date
      FROM fiscal_years
      ORDER BY start_date DESC
    `,
  );
}

/** Fetch one fiscal year by name (`null` if absent). */
export async function getFiscalYear(
  sql: Sql,
  name: string,
): Promise<FiscalYear | null> {
  const rows = await sql<FiscalYear[]>`
    SELECT name, start_date::text AS start_date, end_date::text AS end_date
    FROM fiscal_years
    WHERE name = ${name}
  `;
  return rows[0] ?? null;
}

/**
 * Resolve the fiscal year to display: the one named by `requested` if it exists, otherwise the
 * newest seeded year. Returns `null` only when no fiscal year is seeded at all.
 */
export async function resolveFiscalYear(
  sql: Sql,
  requested?: string,
): Promise<FiscalYear | null> {
  if (requested) {
    const year = await getFiscalYear(sql, requested);
    if (year) return year;
  }
  const rows = await sql<FiscalYear[]>`
    SELECT name, start_date::text AS start_date, end_date::text AS end_date
    FROM fiscal_years
    ORDER BY start_date DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

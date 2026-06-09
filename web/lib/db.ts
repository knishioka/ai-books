import "server-only";

import postgres from "postgres";

/**
 * Read-only data access for the viewer.
 *
 * The viewer is a *read-only* surface (AGENTS.md invariant #1): every query here
 * MUST be a SELECT. All writes/validation flow through the MCP server. The
 * connection string is read from `AI_BOOKS_DB_URL` and stays server-side only —
 * it is never exposed to the client (AC: no secret reaches the browser). In
 * production, point `AI_BOOKS_DB_URL` at a read-only Postgres role so the
 * viewer cannot mutate data even in principle (see web/README.md).
 */

const connectionString = process.env.AI_BOOKS_DB_URL;

// Reuse a single client across hot-reloads / serverless invocations.
const globalForSql = globalThis as unknown as {
  __aiBooksSql?: postgres.Sql;
};

/**
 * The shared read-only SQL client, or `null` when `AI_BOOKS_DB_URL` is unset.
 *
 * Every report query in `lib/reports/*` and `lib/etax/*` goes through this one client so the
 * viewer reuses a small prepared-statement-free pool (pooler-safe). It returns
 * `null` rather than throwing so a page can degrade to a friendly "未接続" banner (e.g. during
 * a CI build with no database) instead of crashing.
 */
export function getSql(): postgres.Sql | null {
  if (!connectionString) {
    return null;
  }
  if (!globalForSql.__aiBooksSql) {
    globalForSql.__aiBooksSql = postgres(connectionString, {
      max: 5,
      idle_timeout: 20,
      // Supabase's pooler (pgbouncer, transaction mode) does not support
      // prepared statements; disabling them keeps the viewer pooler-safe.
      prepare: false,
    });
  }
  return globalForSql.__aiBooksSql;
}

export type AccountType =
  | "asset"
  | "liability"
  | "equity"
  | "revenue"
  | "expense";

export interface Account {
  code: string;
  name: string;
  account_type: AccountType;
  normal_balance: "debit" | "credit";
  is_active: boolean;
}

export type ConnectionResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };

/** Error message shown when the viewer has no database configured. */
export const NO_DB_ERROR =
  "AI_BOOKS_DB_URL が未設定です。Supabase に接続するための値を設定してください。";

/**
 * Run `query` against the shared client, wrapping the result (or a failure reason) in a
 * {@link ConnectionResult}. Centralizes the "no DB configured" and "query threw" handling so
 * every report page renders the same graceful banner instead of a stack trace.
 */
export async function tryQuery<T>(
  query: (sql: postgres.Sql) => Promise<T>,
): Promise<ConnectionResult<T>> {
  const sql = getSql();
  if (!sql) {
    return { ok: false, error: NO_DB_ERROR };
  }
  try {
    return { ok: true, data: await query(sql) };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Unknown database error.",
    };
  }
}

/**
 * Fetch the chart of accounts (勘定科目一覧), ordered by code.
 * Returns a discriminated result so the page can render a friendly state when
 * the database is unreachable or unconfigured (e.g. during CI build).
 */
export function fetchAccounts(): Promise<ConnectionResult<Account[]>> {
  return tryQuery(
    (sql) => sql<Account[]>`
      SELECT code, name, account_type, normal_balance, is_active
      FROM accounts
      ORDER BY code
      LIMIT 500
    `,
  );
}

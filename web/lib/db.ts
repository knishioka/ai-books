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

function getSql(): postgres.Sql | null {
  if (!connectionString) {
    return null;
  }
  if (!globalForSql.__aiBooksSql) {
    globalForSql.__aiBooksSql = postgres(connectionString, {
      max: 1,
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

/**
 * Fetch the chart of accounts (勘定科目一覧), ordered by code.
 * Returns a discriminated result so the page can render a friendly state when
 * the database is unreachable or unconfigured (e.g. during CI build).
 */
export async function fetchAccounts(): Promise<ConnectionResult<Account[]>> {
  const sql = getSql();
  if (!sql) {
    return {
      ok: false,
      error: "AI_BOOKS_DB_URL is not set. Configure it to connect to Supabase.",
    };
  }
  try {
    const rows = await sql<Account[]>`
      SELECT code, name, account_type, normal_balance, is_active
      FROM accounts
      ORDER BY code
      LIMIT 500
    `;
    return { ok: true, data: rows };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Unknown database error.",
    };
  }
}

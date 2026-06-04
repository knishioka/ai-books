/**
 * Small SQL fragments shared across the report queries.
 *
 * `postgres` lets a `sql` fragment nest inside a larger query, so the 取消 (voided) rule —
 * identical in every read — lives here once instead of being copy-pasted (and risking drift)
 * into each query. The rule mirrors the Python repository's `_ENTRY_FILTER`: an explicit
 * `status` matches exactly (so a caller *can* ask for `voided` to audit 取消 entries), while
 * the default (`null`) excludes 取消 so cancelled entries never perturb the active books.
 */

import type { Sql } from "postgres";

import type { EntryStatus } from "./types";

/** The `je.status` predicate fragment for a given (possibly null) status filter. */
export function statusFilter(sql: Sql, status: EntryStatus | null) {
  return sql`(CASE WHEN ${status}::entry_status IS NULL
                   THEN je.status <> 'voided'::entry_status
                   ELSE je.status = ${status}::entry_status END)`;
}

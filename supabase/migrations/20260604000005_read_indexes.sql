-- Migration: read-path indexes for the read-side MCP tools (Issue #15)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- The balance / 総勘定元帳 / journal-list queries all start from "the lines of one
-- account, joined to their entry's date". The existing single-column
-- journal_lines(account_id) index answers the account filter, but the planner
-- then visits the heap for every matching line to read entry_id before the join.
-- A composite (account_id, entry_id) index lets that account-scoped scan feed the
-- join to journal_entries (and its entry_date filter) index-only, which is the hot
-- path for get_account_balance / get_account_ledger and the account filter of
-- list_journal_entries.

CREATE INDEX journal_lines_account_entry_idx
    ON journal_lines (account_id, entry_id);

-- Migration: CSV 取込の重複検知キー (import_hash)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- Backs the bank/CC CSV import tool (Issue #14: import_transactions_csv). Each
-- imported draft entry carries a deterministic hash of its source CSV row so that
-- re-importing the same file never creates duplicates (同一明細の二重取込防止).
--
--   * import_hash is NULL for hand-entered / seed entries; only CSV-imported
--     entries set it. The partial UNIQUE index enforces at-most-once import per
--     row at the storage layer, the backstop behind the service's pre-insert
--     existence check (mirrors voucher_no's partial-unique pattern).

ALTER TABLE journal_entries
    ADD COLUMN import_hash text;  -- CSV 取込元行の指紋 (NULL = 非取込)

-- 取込ハッシュは付与されている場合のみ一意 (NULL は重複可)。
CREATE UNIQUE INDEX journal_entries_import_hash_key
    ON journal_entries (import_hash)
    WHERE import_hash IS NOT NULL;

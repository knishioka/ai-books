-- Migration: journal write-path support (取消 status + 伝票番号 採番)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- Backs the journal write tools (Issue #13: create / update / void / post):
--
--   1. A 'voided' (取消) state for entry_status. Voiding keeps the original row
--      (帳簿の連続性維持) instead of deleting it, and the audit_logs trail records
--      the before/after — together these satisfy the 電子帳簿保存 訂正・削除履歴
--      requirement (AGENTS.md invariant #5).
--   2. void bookkeeping columns on journal_entries (理由 / 取消時刻).
--   3. A sequence backing voucher-number 採番. nextval() is atomic, so concurrent
--      creates never collide (the partial UNIQUE index on voucher_no is the backstop).
--      Sequences are monotonic but may gap on rollback — that is the intended basis
--      for 連番付与 + 欠番検知 downstream.
--
-- PostgreSQL 12+ allows ALTER TYPE ... ADD VALUE inside a transaction as long as the
-- new label is not *used* in the same transaction; this migration only adds it and
-- the column/sequence DDL, so the migration runner's per-file transaction is fine.

ALTER TYPE entry_status ADD VALUE IF NOT EXISTS 'voided';  -- 取消 (物理削除しない)

ALTER TABLE journal_entries
    ADD COLUMN void_reason text,        -- 取消理由 (voided のときのみ)
    ADD COLUMN voided_at   timestamptz; -- 取消時刻 (voided のときのみ)

-- 伝票番号の採番元。明示指定が無いときに connect 側で nextval して付与する。
CREATE SEQUENCE journal_voucher_no_seq AS bigint START WITH 1 INCREMENT BY 1;

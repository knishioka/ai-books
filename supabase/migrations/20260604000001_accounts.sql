-- Migration: accounts (勘定科目 / chart of accounts)
--
-- Forward-only. Once this file has been applied (recorded in schema_migrations)
-- it must never be edited — ship changes as a new migration (AGENTS.md
-- "Never touch" / invariant #3).
--
-- Establishes the account-type taxonomy and the normal-balance side, plus the
-- consistency constraint that ties them together (科目区分 ↔ 正常残高).

-- 科目区分: 資産 / 負債 / 純資産 / 収益 / 費用.
CREATE TYPE account_type AS ENUM ('asset', 'liability', 'equity', 'revenue', 'expense');

-- 借方 / 貸方. Reused as the account's normal-balance side.
CREATE TYPE normal_side AS ENUM ('debit', 'credit');

CREATE TABLE accounts (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code               text NOT NULL UNIQUE,          -- 勘定科目コード
    name               text NOT NULL,                 -- 勘定科目名
    account_type       account_type NOT NULL,         -- 科目区分
    statement_category text,                           -- 集計分類 (青色申告決算書の表示区分)
    normal_balance     normal_side NOT NULL,          -- 正常残高 (借方/貸方)
    parent_id          bigint REFERENCES accounts (id), -- 内訳 (上位科目)
    is_active          boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    -- 科目区分と正常残高の一貫性を ER レベルで強制する:
    --   資産 / 費用 → 借方が正常残高
    --   負債 / 純資産 / 収益 → 貸方が正常残高
    CONSTRAINT accounts_normal_balance_matches_type CHECK (
        (account_type IN ('asset', 'expense') AND normal_balance = 'debit')
        OR (account_type IN ('liability', 'equity', 'revenue') AND normal_balance = 'credit')
    ),

    -- 自分自身を親にできない (内訳ループの最小防止)。
    CONSTRAINT accounts_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE INDEX accounts_parent_id_idx ON accounts (parent_id);

-- Migration: journal_entries (伝票ヘッダ) + journal_lines (明細)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- Double-entry bookkeeping core. Amounts are NUMERIC (浮動小数禁止) so Decimal
-- precision is preserved end to end. Debit/credit *balance* per entry is enforced
-- at the MCP validation layer (AGENTS.md invariant #2), not by a DB constraint;
-- the DB enforces shape, FKs, positivity and status.

-- 借方 / 貸方 (明細の計上方向).
CREATE TYPE entry_side AS ENUM ('debit', 'credit');

-- 伝票の状態. draft = 起票途中, posted = 記帳確定.
CREATE TYPE entry_status AS ENUM ('draft', 'posted');

CREATE TABLE journal_entries (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_date    date NOT NULL,                       -- 取引日
    recorded_date date NOT NULL DEFAULT CURRENT_DATE,  -- 起票日
    description   text,                                 -- 摘要
    voucher_no    text,                                 -- 伝票番号
    source        text NOT NULL DEFAULT 'manual',      -- 起票元 (manual / csv / mcp ...)
    status        entry_status NOT NULL DEFAULT 'draft',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 伝票番号は付与されている場合のみ一意 (NULL は重複可)。
CREATE UNIQUE INDEX journal_entries_voucher_no_key
    ON journal_entries (voucher_no)
    WHERE voucher_no IS NOT NULL;

CREATE INDEX journal_entries_entry_date_idx ON journal_entries (entry_date);

CREATE TABLE journal_lines (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id         bigint NOT NULL REFERENCES journal_entries (id) ON DELETE CASCADE,
    line_no          integer NOT NULL DEFAULT 1,        -- 伝票内の明細順
    account_id       bigint NOT NULL REFERENCES accounts (id),
    side             entry_side NOT NULL,               -- 借方 / 貸方
    amount           numeric(18, 2) NOT NULL,           -- 金額 (NUMERIC, 浮動小数禁止)
    tax_category     text,                               -- 税区分
    sub_account      text,                               -- 補助科目
    line_description text,                               -- 明細摘要
    created_at       timestamptz NOT NULL DEFAULT now(),

    -- 金額は正の値のみ。計上方向は side で表現する。
    CONSTRAINT journal_lines_amount_positive CHECK (amount > 0),
    -- 同一伝票内で明細順は一意。
    CONSTRAINT journal_lines_entry_line_unique UNIQUE (entry_id, line_no)
);

CREATE INDEX journal_lines_entry_id_idx ON journal_lines (entry_id);
CREATE INDEX journal_lines_account_id_idx ON journal_lines (account_id);

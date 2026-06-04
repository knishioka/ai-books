-- Migration: fiscal_years (会計年度) + periods (会計期間)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- Basis for monthly aggregation and 期首/期末 (opening/closing) boundaries.
-- Entries are not hard-linked to a period; a period is resolved from the entry
-- date range at query time, which keeps posting independent of period seeding.

CREATE TABLE fiscal_years (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL UNIQUE,   -- 例: 'FY2026'
    start_date date NOT NULL,          -- 期首
    end_date   date NOT NULL,          -- 期末
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fiscal_years_date_order CHECK (end_date > start_date),
    CONSTRAINT fiscal_years_start_unique UNIQUE (start_date)
);

CREATE TABLE periods (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fiscal_year_id bigint NOT NULL REFERENCES fiscal_years (id) ON DELETE CASCADE,
    name           text NOT NULL,      -- 例: '2026-04'
    start_date     date NOT NULL,
    end_date       date NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT periods_date_order CHECK (end_date >= start_date),
    CONSTRAINT periods_fy_name_unique UNIQUE (fiscal_year_id, name)
);

CREATE INDEX periods_fiscal_year_id_idx ON periods (fiscal_year_id);

# ADR 0005 — `statement_category` を accounts 基本テーブルに置く (pre-release schema shaping)

- Status: Accepted
- Date: 2026-06-04
- Deciders: ai-books maintainers
- Relates to: #10 (Postgres schema, #28), migration `20260604000001_accounts.sql`
- Retroactively recorded: 2026-06-05 (#90). 決定の初出は #10 (#28)。

## Context

The 青色申告決算書 layout needs each account mapped to a **display/aggregation category**
(集計分類 — e.g. which 決算書 line an account rolls up into), distinct from the coarse `account_type`
(資産/負債/純資産/収益/費用). Two questions:

1. **Where does this classification live** — a column on the `accounts` base table, or a separate
   mapping/lookup table?
2. **How was it introduced**, given AGENTS.md invariant #3 says applied migrations are forward-only
   and must never be edited?

The relevant nuance: invariant #3 binds **applied** migrations (those recorded in
`schema_migrations` against a live database). During the **pre-release** period — before any
production database existed and before the schema snapshot was a binding golden — the initial schema
(`20260604000001_accounts.sql`, Issue #10) could still be shaped directly.

## Decision

- `statement_category` is a **nullable `text` column on the `accounts` base table**, carried since
  the initial accounts migration (`20260604000001_accounts.sql`, #10/#28) — not a separate mapping
  table.
- Shaping it into migration 0001 directly was acceptable **specifically because it was pre-release**:
  no migration had been applied to a production database, and no real data existed. This is the
  governing principle, not an exception to invariant #3.
- **Post-release, this door is closed:** once a migration is applied (or the schema golden
  `tests/fixtures/schema/schema.sql` is binding), any change to account classification ships as a
  **new forward-only migration**, never an edit to 0001.

### Why the base table, not a side table

- Classification is a **1:1 intrinsic attribute** of an account (every account has exactly one
  decennial-statement home). A join table would add indirection with no 多重度 to justify it.
- The 決算書 aggregation reads accounts directly; co-locating the category avoids a join on the
  hottest read path and keeps the account row self-describing.

### Alternatives not taken

- **Separate `account_statement_category` mapping table**: rejected — no many-to-many, pure
  overhead for a 1:1 attribute.
- **Add it later as a forward-only migration**: unnecessary while pre-release; would have left 0001
  immediately superseded by 0008 for no benefit. (Had it been post-release, this would be the only
  correct path.)

## Consequences

### Positive

- 決算書 aggregation reads `accounts.statement_category` directly — no join, account row is
  self-describing.
- Initial schema is internally complete; no day-one forward-only patch needed.

### Negative / costs

- Sets a **precedent that must not be over-generalized**: "edit the migration" is permitted _only_
  pre-release. This ADR exists partly to mark that boundary so the convenience is not cited later as
  license to edit applied migrations.
- `statement_category` is nullable `text` (not an enum/FK) — flexible, but classification validity
  is enforced in application/seed code, not by the schema.

### Neutral / unchanged

- Forward-only discipline (invariant #3) is **unchanged** for all applied migrations. This ADR
  clarifies its scope (applied vs pre-release), it does not weaken it.

## Implementation references

- `supabase/migrations/20260604000001_accounts.sql` — `statement_category text` column on `accounts`
  (present since the initial schema commit; the file header restates the forward-only rule).
- `src/ai_books/seed/accounts.py` — assigns each seeded account its `statement_category`.
- `tests/fixtures/schema/schema.sql` — the schema golden that becomes binding post-release
  (drift-detected by `tests/test_schema_snapshot.py`).

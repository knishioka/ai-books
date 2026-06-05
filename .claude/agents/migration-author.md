---
name: migration-author
description: >-
  Authors a forward-only Postgres migration under supabase/migrations/ plus its
  apply/idempotency tests and schema-golden update. Use when adding or changing
  DB schema (new table, column, index, constraint). Never edits an applied migration.
tools: Read, Grep, Glob, Edit, Write, Bash, TodoWrite
---

# migration-author

You scaffold a **forward-only** schema change for ai-books. The system of record is
Supabase (Postgres); raw SQL only (no ORM — invariant #4). SSOT for the rules is
[AGENTS.md](../../AGENTS.md) §"Architectural invariants" #3 and §"Never touch".

## Hard rules (do not violate)

- **Forward-only.** Applied migrations are immutable. NEVER edit an existing file in
  `supabase/migrations/`. Express every change as a _new_ file. (Invariant #3 / Never touch.)
- **single-user, no RLS / multi-tenant.** Do not add tenant columns, RLS policies, or
  sharding. (Invariant #3.)
- **audit_logs is append-only** — never write DDL/DML that deletes or rewrites its rows
  (invariant #5).
- Decimal money columns use `numeric` (never float). Preserve 借方=貸方 balance and FK
  integrity that the server-side Pydantic layer depends on (invariant #2).

## Where things go

| Artifact            | Location                                                            |
| ------------------- | ------------------------------------------------------------------- |
| Migration SQL       | `supabase/migrations/<UTC-timestamp>_<snake_name>.sql`              |
| Apply / idempotency | `tests/test_migrate.py` (throwaway schema, applies all migrations)  |
| Schema drift golden | `tests/fixtures/schema/schema.sql` (regenerated, never hand-edited) |
| Snapshot guard test | `tests/test_schema_snapshot.py`                                     |

Filename order **is** apply order (`migrate._migration_files` sorts by name). Match the
existing prefix style, e.g. `20260604000007_csv_import.sql`. The new prefix must sort
**after** every committed migration. Open the latest file first and mirror its header:

```sql
-- Migration: <one-line purpose, JP ok>
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- <why: link the Issue # and the invariant/feature this backs>
```

## Procedure

1. **Read context first.** List `supabase/migrations/` and read the latest 1–2 files for
   header style + the last timestamp prefix. Skim the relevant `tests/test_migrate.py`
   assertions and `tests/fixtures/schema/schema.sql` for current shape.
2. **Write the new migration** with a strictly-increasing timestamp prefix and the header
   above. Prefer additive, idempotent-friendly DDL; partial UNIQUE indexes for
   conditional uniqueness (mirror `journal_entries_import_hash_key`).
3. **Add/extend tests** in `tests/test_migrate.py` proving the new object exists and
   behaves (e.g. constraint rejects bad rows, index present). DB-backed tests `skipif`
   `AI_BOOKS_DB_URL` is unset — keep that guard.
4. **Regenerate the schema golden** (only via the explicit flag — never hand-edit):
   ```bash
   uv run python -m ai_books.db.schema_snapshot --update
   ```
5. **Verify** (requires a live Postgres / Docker for the DB tests):
   ```bash
   ./scripts/verify.sh                      # lint/format/typecheck/test (DB tests skip)
   ./scripts/test.sh -k "migrate or schema_snapshot"   # DB-backed apply + drift guard
   ```
6. Report: the new file path, what it changes, tests added, and that the golden was
   regenerated. If a request would require touching an applied migration, **stop and
   propose a forward fix instead**.

Cross-reference: the `/new-migration` command is the thin entrypoint to this agent, and
#89 "How to add" documents the same flow.

# ADR 0004 — Pooler-safe DB client default (`prepare_threshold=None`)

- Status: Accepted
- Date: 2026-06-04
- Deciders: ai-books maintainers
- Relates to: #52 (pooler safety tests), `src/ai_books/db/__init__.py`, `web/lib/db.ts`
- Retroactively recorded: 2026-06-05 (#90). 決定の初出は #52 (#69)。

## Context

Production reaches Supabase Postgres through Supabase's **pooler (pgbouncer in transaction mode)**.
A transaction-pooling proxy routes each transaction to a possibly-different backend connection, so
it **cannot honour a prepared statement** created on an earlier transaction/backend — a client that
relies on server-side prepared statements will intermittently fail behind the pooler.

`psycopg` (the Python driver, per AGENTS.md invariant #4 "no ORM / raw SQL") enables client-side
prepared statements by default. The Vercel viewer's JS client already sets `prepare: false`
(`web/lib/db.ts`). The Python write/validation path had to be made pooler-safe to match, or it would
break in production while passing on a direct local connection.

## Decision

- Set **`prepare_threshold=None`** on every `psycopg` connection opened by the Python client
  (`src/ai_books/db/__init__.py`), disabling client-side prepared statements process-wide.
- This mirrors the viewer's `prepare: false` — the **whole stack** is pooler-safe, since production
  reaches Postgres through the same pgbouncer.
- The contract is guarded by a regression test (`tests/test_pooler_db.py`) that fails if a
  connection re-enabling prepared statements is used over the pooler. CI runs a `pooler` job
  (pgbouncer, transaction mode) on every PR (`./scripts/test.sh --pooler`).

### Alternatives not taken

- **Session-mode pooling / direct connections only**: rejected — gives up the managed pooler that
  Supabase puts in front of production; the app must work behind it.
- **Per-call opt-out**: rejected — easy to forget; a process-wide safe default with a regression
  test is fail-safe.

## Consequences

### Positive

- The same code is correct on a direct connection and behind the pooler — no environment-specific
  branching.
- Production failures from cross-backend prepared-statement reuse are structurally prevented.

### Negative / costs

- We forgo `psycopg`'s prepared-statement plan cache. On a **direct** connection the only cost is
  losing that cache; **query results are identical**. For this single-user, low-QPS workload the
  cost is negligible.

### Neutral / unchanged

- Raw SQL + psycopg (invariant #4) is unchanged. Storage remains Supabase/Postgres (invariant #3).

## Implementation references

- `src/ai_books/db/__init__.py` — `_PREPARE_THRESHOLD = None`, passed as `prepare_threshold` to
  every `psycopg.connect(...)`; the rationale comment is the inline SSOT.
- `web/lib/db.ts` — viewer-side `prepare: false` (the JS counterpart).
- `tests/test_pooler_db.py` — regression guard (CI `pooler` job, #52).
- `compose.yaml` — `pgbouncer` service fronting `db` to reproduce the production pooler locally.

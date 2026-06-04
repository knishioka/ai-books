# ai-books viewer (`web/`)

A **read-only** aggregation viewer for `ai-books`, deployed on Vercel. It renders
data stored in Supabase (Postgres) — it does **not** write anything. All
writes/validation flow through the MCP server (see [AGENTS.md](../AGENTS.md)
invariant #1: _read-only viewer only, no data-entry UI_).

- Framework: Next.js (App Router, React Server Components)
- Data: queried server-side from Postgres via [`postgres`](https://github.com/porsager/postgres); the connection string never reaches the browser.

## Screens (read-only)

Every report is rendered from the same Supabase/Postgres data the MCP report layer
(#18–#24) reads, reproducing its golden output (see _Golden cross-check_ below) — the web
side **re-implements no accounting logic beyond the SQL aggregation + the single signing
rule** (`lib/reports/ledger.ts`, the TS twin of `ai_books.ledger.balance_from_totals`). Each
screen has a fiscal-year period selector and a print/PDF layout; none has a write control.

| 画面             | パス                                            | 元データ (golden)           |
| ---------------- | ----------------------------------------------- | --------------------------- |
| 勘定科目一覧     | `/`                                             | `accounts`                  |
| 合計残高試算表   | `/trial-balance`                                | `trial_balance.json`        |
| 月次推移         | `/monthly-trend`                                | `monthly_trend.json`        |
| 仕訳帳           | `/journal`                                      | `journal_book.json`         |
| 総勘定元帳       | `/ledger`                                       | `general_ledger.json`       |
| 損益計算書       | `/pl`                                           | `profit_and_loss.json`      |
| 貸借対照表       | `/bs`                                           | `balance_sheet.json`        |
| 精算表           | `/worksheet`                                    | `worksheet.json`            |
| 青色申告決算書   | `/statements`                                   | `financial_statements.json` |
| e-Tax 取込データ | `/etax` + `/etax/download?fy=…&format=csv\|xml` | `etax_export.json`          |

The data layer lives in `lib/reports/*` (one module per report, each mirroring a
`LedgerRepository` method) and `lib/etax/*` (the spec-driven e-Tax export, the TS twin of
`ai_books.etax`). Amounts are carried as exact integer 銭 (`lib/money.ts`, `bigint`) so a
balance is never a float — the figures match the golden snapshots byte-for-byte.

The generated e-Tax CSV/XML contains the 事業者's 確定数値 (秘密情報); it is streamed as a
download with `Cache-Control: no-store` and never written to disk or committed.

## Local development

Prerequisites: Node 20+ and a running local Supabase (`supabase start` from the
repo root — see the root [README](../README.md#local-postgres-supabase)).

```bash
cd web
npm install
cp .env.example .env.local       # set AI_BOOKS_DB_URL (Supabase `DB URL`)
npm run dev                      # http://localhost:3000
```

`AI_BOOKS_DB_URL` defaults to the Supabase CLI's local Postgres
(`postgresql://postgres:postgres@127.0.0.1:54322/postgres`). The page shows a
connection banner: green when it reaches the database, amber (with the error)
when it cannot — so a missing DB degrades gracefully instead of crashing.

Checks:

```bash
npm run lint        # eslint (next/core-web-vitals + next/typescript)
npm run typecheck   # tsc --noEmit
npm run test        # vitest (fast, DB-free unit layer — see below)
npm run build       # next build (does not require a database)
```

### Unit tests (fast, no database)

`npm run test` runs the [Vitest](https://vitest.dev) unit layer over the **pure** data logic
(`lib/reports/*`, `lib/etax/*`, `lib/money.ts`, `lib/format.ts`) — no database, milliseconds.
It pins the 符号則・段階利益・科目振り分け・月次タイリング・端数/桁数検証 edge cases
(空 FY・片側のみ・unclassified・期首/期末境界・月跨ぎ) so a regression is caught immediately,
complementing the heavier golden cross-check below (which stays the source of truth for
end-to-end numbers). Aggregation reports are exercised with a small in-memory `sql` stand-in, so
no DB is needed.

```bash
npm run test            # run once
npm run test:watch      # watch mode
npm run test:coverage   # + v8 coverage report
```

### Golden cross-check (numbers match the report layer)

`npm run verify:golden` asserts the viewer's data layer reproduces the Python report layer's
**golden snapshots** (`tests/fixtures/seed_fy/golden/*.json`, #17) exactly — the same
cross-check the pytest harness runs, but from the TS side. It needs a Postgres seeded with the
synthetic FY2025 fixture; from the repo root:

```bash
# 1. a throwaway Postgres (any local Postgres works; example uses Docker)
docker run -d --name aibooks-verify -e POSTGRES_PASSWORD=postgres -p 55450:5432 postgres:16
export AI_BOOKS_DB_URL=postgresql://postgres:postgres@127.0.0.1:55450/postgres

# 2. migrate + seed FY2025 through the production write path
PYTHONPATH=. uv run python scripts/seed_verify_db.py

# 3. compare every report (試算表 … 決算書 … e-Tax) to its golden file
cd web && npm run verify:golden          # ✓ per report, non-zero exit on any diff
```

This is a local gate (CI runs `lint` / `build`, which need no database). A mismatch prints a
path-tagged diff so a sign flip or a dropped row is caught the same way the Python harness
catches it.

## Deploying to Vercel

1. Create a Vercel project linked to this repo and set **Root Directory** to `web`.
2. Add the `AI_BOOKS_DB_URL` environment variable for the Preview and Production
   environments. Switching its value switches which database the viewer reads —
   e.g. a Supabase cloud connection string for Production.
3. Push a branch → Vercel builds a **Preview** deployment automatically.

### Use a read-only database role (recommended for Production)

The viewer only ever runs `SELECT`s, but for defence in depth point
`AI_BOOKS_DB_URL` at a Postgres role that _cannot_ write. The committed,
idempotent grant script [`supabase/roles/viewer_readonly.sql`](../supabase/roles/viewer_readonly.sql)
creates `viewer_ro` with `SELECT` on every current **and future** table and
nothing else (no `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`):

```bash
# apply with an admin connection, then give the role a login + password
psql "$ADMIN_DB_URL" -v ON_ERROR_STOP=1 -f supabase/roles/viewer_readonly.sql
psql "$ADMIN_DB_URL" -c "ALTER ROLE viewer_ro WITH LOGIN PASSWORD '<strong-password>';"
```

Then set `AI_BOOKS_DB_URL` to `viewer_ro`'s connection string in Vercel. The
grant set is generated from `tests/fixtures/readonly.py` and enforced
mechanically: `tests/test_readonly_db.py` proves the role can run every viewer
read (golden match included) but is denied every write, and
`tests/test_readonly_role.py` guards the script against drift. Run them with
`./scripts/test.sh -k readonly`.

## Security notes

- `AI_BOOKS_DB_URL` is server-side only — it is never bundled into client code
  (`postgres` is listed in `serverExternalPackages`, and `lib/db.ts` imports
  `server-only`).
- No service-role key or other secret is shipped to the browser.
- There is no write UI; the viewer cannot create or edit data.

# ai-books viewer (`web/`)

An **authenticated, read-only** aggregation viewer for `ai-books`, deployed on
Vercel. It renders data stored in Supabase (Postgres) — it does **not** write
anything. All writes/validation flow through the MCP server (see
[AGENTS.md](../AGENTS.md) invariant #1: _read-only viewer only, no data-entry UI_).

Every route is gated behind **Supabase Auth** login (issue #108 / [ADR 0008](../docs/adr/0008-remote-mcp-single-tenant-auth.md)):
an unauthenticated visitor is redirected to `/login`, and only the single
configured owner may view. Login restricts _who can read_; writes stay impossible
regardless of login via the `viewer_ro` DB role (defence in depth). This is **not**
multi-tenant — there is one owner and one dataset (AGENTS.md invariant #3).

- Framework: Next.js (App Router, React Server Components)
- Data: queried server-side from Postgres via [`postgres`](https://github.com/porsager/postgres); the connection string never reaches the browser.
- Auth: Supabase Auth (email + password) via [`@supabase/ssr`](https://github.com/supabase/auth-helpers); the gate lives in `web/proxy.ts` (Next's proxy/middleware convention).

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
cp .env.example .env.local       # set AI_BOOKS_DB_URL + the Supabase Auth values
npm run dev                      # http://localhost:3000 → redirects to /login
```

`.env.local` needs both the DB URL and the **Supabase Auth** values (the login gate is
fail-closed — with auth unconfigured, every route redirects to `/login`):

| Variable                        | Purpose                                                                |
| ------------------------------- | ---------------------------------------------------------------------- |
| `AI_BOOKS_DB_URL`               | Postgres connection (server-side; use `viewer_ro` in production)       |
| `NEXT_PUBLIC_SUPABASE_URL`      | Supabase project URL (public — from `supabase start` / Vercel)         |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon / publishable key (public; **not** the service-role key) |
| `AUTH_ALLOWED_EMAIL`            | Single-owner allowlist; a different identity is denied (fail closed)   |

There is **no sign-up UI** (single user): provision the owner account in the Supabase
dashboard (Authentication → Users), then log in at `/login`. `AUTH_ALLOWED_EMAIL` is an
extra authorization layer — leave it unset to allow any authenticated Supabase user, or
set it to the owner's email to deny everyone else even with a valid Supabase token.

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

### E2E browser smoke + fail-closed auth (Playwright)

`e2e/` holds the [Playwright](https://playwright.dev) smoke harness (#162). It proves the thing
the golden cross-check cannot: that every one of the 10 screens actually **renders in a real
browser**, and that the auth gate is **fail closed** — an unauthenticated or non-allowlisted
visitor is redirected to `/login` and sees no data. It asserts headings and redirects, never
figures (numbers stay the golden's job). Auth runs against **real Supabase Auth (GoTrue)** with
no test-only bypass (invariant #1 / ADR-0008).

The specs are excluded from the Next.js build (`tsconfig.json` `exclude`) so the isolated Vercel
parity build never compiles them. Run the whole flow — `supabase start`, seed, build + serve,
Playwright — from the repo root:

```bash
./scripts/test.sh --e2e          # boots Supabase, seeds, builds the viewer, runs the specs
```

Or, with a local `supabase start` stack already running and its env exported, iterate from `web/`:

```bash
cd web && npx playwright test    # reuses the running dev/prod server (see playwright.config.ts)
```

CI runs the identical harness in the `web-e2e` job.

### Visual regression (決算書 preview + print layout)

`e2e/visual.spec.ts` pins the **layout** of the 青色申告決算書 preview (`/statements`) and two
representative reports (試算表 / 損益計算書) with Playwright's `toHaveScreenshot` — including the
`@media print` rules in `globals.css`, captured via `emulateMedia({ media: "print" })` (issue #165).
The golden cross-check owns the _numbers_ and the smoke specs own _does-it-render_; this owns _does
the layout silently break_, the only mechanical guard before a 崩れた preview reaches a printed 提出資料.
Only the synthetic `seed_fy` fixture is ever captured — never real 確定数値.

Pixel baselines are **platform-bound**: macOS font rendering differs from Linux, so a baseline
generated on a Mac would mismatch CI forever. The `visual` project is therefore **gated behind
`PLAYWRIGHT_VISUAL=1`** and only ever runs **inside the pinned `mcr.microsoft.com/playwright`
container** — the same Linux environment, locally and in CI's `web-e2e` job — so the committed
`*-linux.png` baselines always match. A bare `npx playwright test` (e.g. on macOS) skips it entirely.

Prerequisite: a running local Supabase stack (`supabase start`). From `web/`:

```bash
npm run e2e:visual          # compare against the committed baselines (what CI runs)
npm run e2e:visual:update   # (re)create baselines after an INTENTIONAL layout change
```

`e2e:visual:update` is also how you bootstrap the baselines; **review the regenerated PNGs by eye**
before committing — a screenshot test can only prove the layout has not changed, never that the
captured layout is correct. The script seeds the fixture on the host, then builds + serves the
viewer and drives Chromium inside the container (reaching the host stack via `host.docker.internal`).

## Deploying to Vercel

1. Create a Vercel project linked to this repo and set **Root Directory** to `web`.
2. Add the `AI_BOOKS_DB_URL` environment variable for the Preview and Production
   environments. Switching its value switches which database the viewer reads —
   e.g. a Supabase cloud connection string for Production.
3. Add the Supabase Auth variables (`NEXT_PUBLIC_SUPABASE_URL`,
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `AUTH_ALLOWED_EMAIL`) for both environments —
   without them the gate fails closed and every route redirects to `/login`.
4. Push a branch → Vercel builds a **Preview** deployment automatically.

### Deploying from the CLI (run from the repository root)

The project's **Root Directory is `web`**, so the Vercel CLI must be run from the
**repository root**, _not_ from `web/`. Running `vercel deploy` inside `web/` makes Vercel
apply the `web` root twice and double the path (`web/web/.next` locally,
`/vercel/path0/path0/.next` remotely): the build succeeds but the deploy fails with
`ENOENT … .next/package.json`. From the repo root:

```bash
vercel deploy --prod --scope <team>   # e.g. --scope d-stats
```

(`vercel link` creates a `.vercel/` dir; it is gitignored at the repo root.)

### Public demo sample (synthetic data)

Set `AI_BOOKS_VIEWER_PUBLIC=true` (server-side env) to serve the read-only viewer
**without** the single-user auth gate — used for the hosted demo. Writes stay impossible
regardless via the `viewer_ro` role.

Seed the demo fiscal years through the production write path. This needs a **write-capable**
`AI_BOOKS_DB_URL` (the `postgres` role, **not** `viewer_ro`, which is read-only):

```bash
# fresh / throwaway DB — migrate + seed all demo years in one shot:
PYTHONPATH=. AI_BOOKS_DB_URL="<write-role connection string>" \
  uv run python scripts/seed_verify_db.py --public-sample

# already-provisioned DB (e.g. the live cloud project, schema already applied) —
# run only the loader (migrate is for throwaway DBs and errors on an existing schema):
PYTHONPATH=. AI_BOOKS_DB_URL="<write-role connection string>" uv run python - <<'PY'
from ai_books.db import transaction
from tests.fixtures.seed_fy.public_sample import load_public_sample_years
with transaction() as conn:            # transaction() commits on context exit
    print(load_public_sample_years(conn))
PY
```

The loader (`tests/fixtures/seed_fy/public_sample.py`) is idempotent (voucher-no keyed) and
seeds **FY2025** (KOA210 一般用) + **FY2023-KOA220** (不動産所得) + **FY2024-KOA240** (農業所得),
date-shifted into separate fiscal years so a single sample year never mixes the three
businesses in date-range report queries.

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
- No service-role key or other secret is shipped to the browser. Only the Supabase
  **anon** (publishable) key and project URL are public, by design — they cannot
  write and carry no privileged access; `AUTH_ALLOWED_EMAIL` stays server-side.
- The login gate (`web/proxy.ts`) is **fail-closed**: missing/expired/forged
  sessions and non-owner identities are denied, and the session is verified with
  `supabase.auth.getUser()` (revalidated against Supabase, not just cookie-decoded).
- There is no write UI and no sign-up UI; the viewer cannot create or edit data,
  and writes remain blocked at the DB by the `viewer_ro` role independently of login.

# ai-books

> **AI-first accounting MCP server** — an interface for AI agents, not for humans.

`ai-books` exposes double-entry bookkeeping primitives (chart of accounts, journal entries, trial balance, financial statements) as [Model Context Protocol](https://modelcontextprotocol.io/) tools. The premise: if your accounting system has a great machine interface, the human UI is just a thin **read-only** aggregation dashboard.

Architecture in one line: **MCP = the write/validation interface · Supabase (Postgres) = storage · Vercel = read-only viewer.** See [ADR 0001](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md) for the rationale.

## Why

Most accounting software puts a heavy web UI front and center, with the API as an afterthought. `ai-books` inverts this — the MCP is the primary interface. AI agents (Claude, ChatGPT, Codex, …) can:

- create and validate journal entries (借方貸方 balance enforced at the validation layer)
- query account balances, trial balance, P/L, B/S
- import bank / CC CSV via tool calls
- run ad-hoc aggregations

Humans just look at the read-only Vercel viewer (no data entry there — all writes flow through MCP). The end goal is producing the **青色申告決算書 + e-Tax import data** (the tax-amount computation itself stays in downstream tools).

## Status

🚧 **M0** — bootstrap. Only a `hello` smoke-test tool is implemented. The project has just pivoted (see [ADR 0001](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md)): storage moves from local SQLite to **Supabase (Postgres)**, a **read-only Vercel viewer** is added, and **青色申告決算書 + e-Tax 取込データ output** is now an in-scope goal. Schema, accounting tools, viewer, and reports land in the [roadmap issues](#roadmap).

## Quick start (M0)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/knishioka/ai-books.git
cd ai-books
uv sync
uv run pre-commit install          # enable pre-commit hooks (first time only)
uv run python -m ai_books.server   # starts MCP server on stdio
```

Run the verification suite (lint / format / typecheck / test):

```bash
./scripts/verify.sh
```

Pre-commit hooks (ruff + hygiene checks) run automatically on `git commit`.
To run them across the whole repo:

```bash
uv run pre-commit run --all-files
```

## Local Postgres (Supabase)

Storage is **Supabase (Postgres)** (see [ADR 0001](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md)).
For local development, the [Supabase CLI](https://supabase.com/docs/guides/local-development)
runs Postgres + Studio on Docker — so local and production share the same engine
(no "SQLite locally, Postgres in prod" drift).

Requires Docker and the Supabase CLI (`brew install supabase/tap/supabase`).

```bash
supabase start                     # boots Postgres + Studio on Docker
```

`supabase start` prints connection details. Copy the **DB URL** into your `.env`
(create it from [.env.example](./.env.example) — `.env` is gitignored):

```bash
cp .env.example .env
# Set AI_BOOKS_DB_URL to the "DB URL" from `supabase start`. The CLI default is:
#   AI_BOOKS_DB_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
# Also set SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY from the
# `API URL` / `anon key` / `service_role key` lines of the same output.
```

Connectivity smoke test (`SELECT 1`):

```bash
uv run python -c "from ai_books import db; print(db.ping())"   # -> 1
```

`supabase stop` tears the stack down. The smoke test in `tests/test_db.py` runs
only when `AI_BOOKS_DB_URL` is set, so `./scripts/verify.sh` stays green even
without a running database; CI runs it against a Postgres service container.

## Web viewer (Vercel, read-only)

A **read-only** aggregation viewer lives in [`web/`](./web) (Next.js, deployed on
Vercel). It renders Supabase/Postgres data — chart of accounts today, ledgers and
financial statements later — and has **no data-entry UI** (writes flow through MCP;
see [AGENTS.md](./AGENTS.md) invariant #1). Data is queried server-side, so no
database credential ever reaches the browser.

Run it against your local Supabase (`supabase start` must be running):

```bash
cd web
npm install
cp .env.example .env.local         # set AI_BOOKS_DB_URL (the `DB URL` from supabase start)
npm run dev                        # http://localhost:3000
```

Full local + Vercel deployment instructions (including the recommended read-only
DB role for production) are in [web/README.md](./web/README.md).

## Schema & migrations

The system of record is defined by **forward-only SQL migrations** under
[`supabase/migrations/`](./supabase/migrations). They establish the double-entry
schema: `accounts` (chart of accounts, with a CHECK that keeps 科目区分 ↔ 正常残高
consistent), `journal_entries` / `journal_lines` (amounts are `NUMERIC`, never
float), `fiscal_years` / `periods`, and an **append-only** `audit_logs` table
(UPDATE/DELETE/TRUNCATE are rejected by trigger).

Apply pending migrations with the runner (idempotent — re-running is a no-op):

```bash
uv run python -m ai_books.db.migrate            # uses AI_BOOKS_DB_URL
uv run python -m ai_books.db.migrate --help     # --migrations-dir / --database-url
```

The runner records each applied file in a `schema_migrations` table and applies
only the ones not yet recorded, in filename order.

**Forward-only — no rollback step.** Applied migration files are never edited or
deleted (AGENTS.md "Never touch" / invariant #3). To undo or change something,
**add a new migration** that counteracts it (e.g. a later file that drops a column
or adjusts a constraint). This keeps every environment reproducible by replaying
the same ordered list from a clean database.

## Seed: chart of accounts

The standard 個人事業/青色申告 chart of accounts (科目区分・表示区分・正常残高・内訳
親子, plus the 製造原価 accounts) is seeded by a loader. It validates the data —
区分 ↔ 正常残高 consistency and 表示区分 coverage — _before_ writing, and is
idempotent (`ON CONFLICT (code) DO NOTHING`), so re-running never duplicates:

```bash
uv run python -m ai_books.seed.accounts   # uses AI_BOOKS_DB_URL (run after migrate)
```

Read it back over MCP with `list_accounts` (filter by 区分 / 表示区分 / 有効),
`get_account` (by 勘定科目コード), and `search_accounts` (code/名称 substring) —
all returning typed `Account` rows.

## Synthetic seed + golden snapshots (tests)

検証コストを集中投資する土台。架空の製造業個人事業主の **1 会計年度** 分の合成仕訳
(売上 / 仕入 / 製造原価 / 経費 / 固定資産 / 減価償却 / 借入 / 家事按分 / 期末整理) と、
それを通したレポートの **ゴールデンスナップショット** (期待値 JSON) を
[`tests/fixtures/seed_fy/`](./tests/fixtures/seed_fy/README.md) に置く。集計 (#18) 以降の
レポートは、この seed/golden を再利用して差分だけで自動検証できる。

```bash
uv run python -m tests.fixtures.seed_fy            # ゴールデン確認 (dry-run)
uv run python -m tests.fixtures.seed_fy --update   # 更新は明示フラグでのみ (誤上書き防止)
```

ゴールデンは DB 不要で生成でき、pytest ハーネスは DB ロード結果を SQL 集計してゴールデンと
比較する。設計意図と手計算で追える期末残高は上記 README に記載。

## Use with Claude Desktop

A reference `claude-desktop-config.json` lands in Issue #5. The shape will be:

```jsonc
{
  "mcpServers": {
    "ai-books": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ai-books",
        "run",
        "python",
        "-m",
        "ai_books.server",
      ],
    },
  },
}
```

## Roadmap

Direction is set by [ADR 0001](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md). The
original SQLite-oriented plan is re-scoped onto Supabase (Postgres) + a Vercel read-only viewer:

| #   | Title                                                           | Status after ADR 0001                                |
| --- | --------------------------------------------------------------- | ---------------------------------------------------- |
| 1   | feat: bootstrap schema and forward-only migration runner        | Re-scoped → Supabase (Postgres)                      |
| 2   | feat: read-side MCP tools (accounts, journal entries, balances) | Retained                                             |
| 3   | feat: write-side MCP tools with debit/credit validation         | Retained                                             |
| 4   | feat: aggregation tools (trial balance, P/L, B/S)               | Retained / extended toward 青色申告決算書            |
| 5   | docs: README, Claude Desktop integration, synthetic seed data   | Retained / extended (Vercel viewer + Supabase setup) |

Supabase provisioning, the Vercel viewer, and 青色申告決算書 + e-Tax export are tracked by
issues #9, #10, #11 and later. Track progress: [open issues](https://github.com/knishioka/ai-books/issues).

## Design influences

- `simple-bookkeeping` (archived) — DB schema design (chart of accounts, debit/credit normalization, audit log)
- `ib-sec-mcp` — Python + FastMCP + SQLite reference pattern

## Non-goals (forever)

- Web UI for data entry / editing — writes flow exclusively through MCP; the Vercel viewer is **read-only**
- Multi-tenant SaaS / RLS — single-user; Supabase (Postgres) is for durable storage, not multi-tenancy
- Tax-amount computation / filing engine — `ai-books` produces the ledger and the **青色申告決算書 + e-Tax import data**; computing the tax owed and submitting it belong in downstream tools

## License

MIT — see [LICENSE](./LICENSE).

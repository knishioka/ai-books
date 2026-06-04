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

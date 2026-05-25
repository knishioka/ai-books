# ai-books

> **AI-first accounting MCP server** — an interface for AI agents, not for humans.

`ai-books` exposes double-entry bookkeeping primitives (chart of accounts, journal entries, trial balance, financial statements) as [Model Context Protocol](https://modelcontextprotocol.io/) tools. The premise: if your accounting system has a great machine interface, the human UI is just a thin aggregation dashboard.

## Why

Most accounting software puts a heavy web UI front and center, with the API as an afterthought. `ai-books` inverts this — the MCP is the primary interface. AI agents (Claude, ChatGPT, Codex, …) can:

- create and validate journal entries (借方貸方 balance enforced at the validation layer)
- query account balances, trial balance, P/L, B/S
- import bank / CC CSV via tool calls
- run ad-hoc aggregations

Humans just look at the generated reports.

## Status

🚧 **M0** — bootstrap. Only a `hello` smoke-test tool is implemented. Schema, accounting tools, and reports land in the [initial roadmap issues](#roadmap).

## Quick start (M0)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/knishioka/ai-books.git
cd ai-books
uv sync
uv run python -m ai_books.server   # starts MCP server on stdio
```

Run the verification suite:

```bash
./scripts/verify.sh
```

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

| #   | Title                                                           |
| --- | --------------------------------------------------------------- |
| 1   | feat: bootstrap SQLite schema and minimal migration runner      |
| 2   | feat: read-side MCP tools (accounts, journal entries, balances) |
| 3   | feat: write-side MCP tools with debit/credit validation         |
| 4   | feat: aggregation tools (trial balance, P/L, B/S)               |
| 5   | docs: README, Claude Desktop integration, synthetic seed data   |

Track progress: [open issues](https://github.com/knishioka/ai-books/issues).

## Design influences

- `simple-bookkeeping` (archived) — DB schema design (chart of accounts, debit/credit normalization, audit log)
- `ib-sec-mcp` — Python + FastMCP + SQLite reference pattern

## Non-goals (forever)

- Web UI for data entry / editing — the MCP and a static report dashboard are the entire UX
- Multi-tenant SaaS / RLS — single-user, single-file SQLite forever
- Tax filing engine — `ai-books` produces the ledger; tax computation belongs in downstream tools

## License

MIT — see [LICENSE](./LICENSE).

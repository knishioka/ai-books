---
description: Scaffold an aggregation/report (snapshot + format) with seed_fy golden + DB↔Web cross-check.
argument-hint: "<report_name>"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Task
---

Scaffold a new read-only report named `$ARGUMENTS`.

Delegate to the **report-author** subagent. Pass the requested name and these
non-negotiables (SSOT: [AGENTS.md](../../AGENTS.md) invariant #1; numeric-identity guarantee):

- Aggregation/query in `src/ai_books/aggregation.py` / `db/repository.py`; machine-readable
  `$ARGUMENTS_snapshot()` in `src/ai_books/reports/format.py` (export from `reports/__init__`).
  Add an `@mcp.tool` only if it's a new MCP capability (then also follow mcp-tool-author rules).
- **Decimal precision, no float drift.** Render-only — no write path / data-entry UI.
- Offline reducer + golden in `tests/fixtures/seed_fy/`; regenerate goldens with the explicit
  flag only — **never hand-edit**: `uv run python -m tests.fixtures.seed_fy --update`.
- If the viewer renders it: reproduce in `web/lib/reports/$ARGUMENTS.ts` (+ test) so the
  **DB↔Web golden cross-check** covers it.

Verify before reporting:

```bash
./scripts/verify.sh
./scripts/test.sh -k "reports or seed_fy"
./scripts/test.sh --web        # viewer golden numeric parity
```

See #89 "How to add". Report the snapshot/tool added, web parity status, and that goldens
were regenerated.

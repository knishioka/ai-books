---
name: report-author
description: >-
  Adds an aggregation/report (snapshot + format) with its seed_fy golden and the
  DB↔Web golden cross-check. Use when adding a financial report or aggregation
  (trial balance / P&L / B/S / ledger / 決算書-style view). Keeps numbers identical
  across Python and the Vercel viewer.
tools: Read, Grep, Glob, Edit, Write, Bash, TodoWrite
---

# report-author

You add a read-only aggregation/report to ai-books. Reports are **render/aggregate only** —
no write path, no data-entry UI (invariant #1). The defining guarantee is **numeric
identity**: the Python report and the Vercel viewer's TypeScript reproduction must agree on
every figure, pinned by golden snapshots. SSOT: [AGENTS.md](../../AGENTS.md).

## Hard rules

- **Decimal precision everywhere** — `Decimal` in Python, no float drift; money formatting
  goes through `reports.money` / the snapshot helpers, not ad-hoc string ops.
- **Golden files change only via the explicit `--update` path** — never hand-edit a golden.
- **DB↔Web parity**: any new report that the viewer renders must also be reproduced in
  `web/lib/reports/` and covered by the golden cross-check, or numbers can silently diverge.
- Read-only: the report reads via repositories; it must not write or require a write UI.

## Layering (mirror existing reports)

| Layer                  | Location                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------ |
| Aggregation / query    | `src/ai_books/aggregation.py`, `src/ai_books/db/repository.py`, models in `models/`  |
| Machine-readable shape | `src/ai_books/reports/format.py` → `*_snapshot()` (exported from `reports/__init__`) |
| MCP tool surface       | `src/ai_books/server.py` (`@mcp.tool`, e.g. `trial_balance`, `general_ledger`)       |
| Web reproduction       | `web/lib/reports/<report>.ts` (+ `*.test.ts`)                                        |
| Golden harness         | `tests/fixtures/seed_fy/` (synthetic FY dataset + offline reducer + golden)          |
| Tests                  | `tests/test_reports.py`, `tests/test_seed_fy.py`, `tests/test_seed_fy_db.py`         |

## Procedure

1. **Read first**: an existing report end-to-end — e.g. `profit_and_loss_snapshot` in
   `reports/format.py`, its server tool, its `web/lib/reports/profit-and-loss.ts`, and how
   `tests/fixtures/seed_fy/` builds + compares its golden (`load_golden`, `diff_snapshots`).
2. **Implement aggregation → snapshot** in Python; export the `*_snapshot()` from
   `reports/__init__.py`. Add an `@mcp.tool` only if the report is a new MCP capability
   (then also follow the mcp-tool-author rules).
3. **Add the offline reducer + golden** in `tests/fixtures/seed_fy/` so the report is
   computed from the synthetic dataset, then regenerate goldens (explicit flag only):
   ```bash
   uv run python -m tests.fixtures.seed_fy --update
   ```
4. **Reproduce in the viewer**: add `web/lib/reports/<report>.ts` + unit test so the web
   golden cross-check covers it.
5. **Verify**:
   ```bash
   ./scripts/verify.sh                 # Python lint/format/typecheck + DB-free golden freshness
   ./scripts/test.sh -k "reports or seed_fy"   # DB-backed report round-trip
   ./scripts/test.sh --web             # viewer golden numeric cross-check (DB↔Web parity)
   ```
6. Report: the report name, the snapshot/tool added, web parity status, and that goldens
   were regenerated (not hand-edited).

Cross-reference: `/new-report` is the thin entrypoint; #89 "How to add" mirrors this flow.

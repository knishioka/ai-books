---
description: One-command local guarantee — runs ./scripts/test.sh --all (every block + PASS/FAIL summary).
argument-hint: "[--down]"
allowed-tools: Bash(./scripts/test.sh:*)
---

Run the **single, mechanical "everything works locally" check** (#59).

```bash
./scripts/test.sh --all $ARGUMENTS
```

Brings up Postgres + the pgbouncer pooler once, then runs every guarantee block and ends
with a PASS/FAIL summary. Unlike `/test`, it does **not** stop at the first failure — every
block runs so the summary shows the full picture, and the command exits non-zero if any
block failed. Blocks (each maps 1:1 to a CI job — see [README](../../README.md) /
[AGENTS.md](../../AGENTS.md)):

1. **Python full suite + coverage gate** (direct DB) — all DB-backed pytest (MCP, property,
   read-only role) + the line 80 / branch 70 gate → CI `verify`.
2. **Web unit layer + coverage gate** (vitest) → CI `web`.
3. **Viewer golden cross-check** (direct DB) → CI `web-golden`.
4. **Pooler safety suite + golden** (through pgbouncer) → CI `pooler`.

- Requires Docker. Containers are reused across runs; `--down` stops them.
- lint / format / typecheck live in `/verify`; the two together cover every CI job.

Report the PASS/FAIL summary verbatim and, on any failure, the failing block's output.

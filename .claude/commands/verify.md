---
description: Verify the repo — runs ./scripts/verify.sh (lint / format / typecheck / test).
argument-hint: "[--json]"
allowed-tools: Bash(./scripts/verify.sh:*)
---

Run the ai-books verification entrypoint and report the result.

```bash
./scripts/verify.sh $ARGUMENTS
```

- Single source of truth for verification (see [AGENTS.md#verification](../../AGENTS.md)).
- Runs lint → format → typecheck → test in order (build is `n/a` — this is a library).
- DB-backed tests are **skipped** when `AI_BOOKS_DB_URL` is unset; the run still passes.
  Use `/test` for the full DB-backed suite.
- Pass `--json` for structured output (CI / Codex).
- Exit codes: `0` all pass · `1` one or more fail · `2` environment error.

Summarize pass/fail per step. On failure, surface the failing step's output.

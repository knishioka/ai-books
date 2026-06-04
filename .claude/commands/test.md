---
description: Full test suite against a real Postgres — runs ./scripts/test.sh (DB-backed tests included).
argument-hint: "[--web] [--down] [pytest args...]"
allowed-tools: Bash(./scripts/test.sh:*)
---

Run the full ai-books test suite — including the DB-backed tests that `/verify` skips.

```bash
./scripts/test.sh $ARGUMENTS
```

- Boots one lightweight `postgres:17-alpine` container (compose service `db`) — no full
  `supabase start` needed. Requires Docker. The container is reused across runs.
- `--web` also cross-checks the Vercel viewer's numbers against golden.
- `--down` stops & removes the test container, then exits.
- Extra args are forwarded to pytest (e.g. `/test -k etax -x`).
- If `AI_BOOKS_DB_URL` is already set (e.g. CI), it is honoured as-is and no container starts.

Report pass/fail and, on failure, the relevant pytest output.

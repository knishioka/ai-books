---
description: Scaffold a forward-only Postgres migration + apply/idempotency tests + schema golden.
argument-hint: "<snake_name>"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Task
---

Scaffold a new **forward-only** migration named `$ARGUMENTS`.

Delegate to the **migration-author** subagent. Pass the requested name and these
non-negotiables (SSOT: [AGENTS.md](../../AGENTS.md) invariant #3 / Never touch):

- New file `supabase/migrations/<UTC-timestamp>_$ARGUMENTS.sql` whose prefix sorts **after**
  every committed migration. **Never edit an applied migration** — forward-only only.
- Header mirrors the latest migration's ("Forward-only. Do not edit after applied…", Issue #).
- Add/extend apply + idempotency assertions in `tests/test_migrate.py` (keep the
  `AI_BOOKS_DB_URL` skip guard).
- Regenerate the schema golden — **explicit flag only, never hand-edit**:
  `uv run python -m ai_books.db.schema_snapshot --update`.

Verify before reporting:

```bash
./scripts/verify.sh
./scripts/test.sh -k "migrate or schema_snapshot"
```

Generated artifacts, required tests, and verification commands are also documented in
#89 "How to add". Report the new file, tests added, and that the golden was regenerated.

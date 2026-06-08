# ADR 0009 — Retain dormant remote MCP code

- Status: Accepted
- Date: 2026-06-08
- Deciders: ai-books maintainers
- Relates to: #142 / #104 / #106 / #107 / #109 / #110 / [ADR 0008](./0008-remote-mcp-single-tenant-auth.md)

## Context

The remote-publish track (Epic #104) was planned to expose the same MCP tools over
the Web. ADR 0008 fixed the required posture before implementation: Streamable
HTTP is opt-in, stdio remains the default, remote requests require Supabase Auth,
and authorization is restricted to a single-user allowlist.

Parts of that track were implemented and merged:

- `src/ai_books/server.py` gained optional Streamable HTTP transport and a
  fail-closed launch guard.
- `src/ai_books/auth.py` gained the Supabase JWT provider and single-user
  allowlist gate.
- `tests/test_server_http.py`, `tests/test_auth.py`, and
  `tests/test_auth_audit_db.py` defend the auth boundary and audit actor wiring.
- `docs/remote/deploy-runbook.md` records a public deployment/runbook path.

The product direction later settled on a local-first operating policy, and the
remote track (#104/#109) was closed as not planned. That left a choice: remove the
HTTP/Auth code and tests, or retain them as dormant, fail-closed restart code.

The key constraints are unchanged:

- stdio local usage is the normal operating mode.
- Public remote MCP must never open without authentication.
- Viewer-side Supabase Auth (#108) is a separate read-only viewer concern and is
  not part of this decision.
- Authentication still does not imply multi-tenancy (ADR 0008 / invariant #3).

## Decision

Retain the remote MCP HTTP/Auth implementation as dormant code.

The retained path is not the active operating mode. `AI_BOOKS_MCP_TRANSPORT`
still defaults to `stdio`; running HTTP still requires an explicit opt-in; and
HTTP startup still fails closed unless the Supabase Auth provider and single-user
allowlist are configured. Reopening public remote MCP requires a new explicit
decision/review and an updated runbook acceptance pass.

We choose retention over removal because the code is already guarded by a
fail-closed startup boundary and defended by tests, while removing it would add
rollback cost if remote MCP is revisited. The maintenance cost is limited by
documenting the path as dormant and keeping the existing auth-boundary tests in
the standard verification suite.

ADR 0008 remains the posture contract for any remote MCP exposure. This ADR adds
the current operational decision: remote MCP is on hold, but the fail-closed code
is intentionally left in place for future restart.

## Consequences

### Positive

- Future remote MCP work can restart from a tested fail-closed baseline instead
  of recovering code from history.
- The repository no longer has ambiguous "implemented but maybe active" remote
  documentation; README, AGENTS.md, code comments, and the runbook name the dormant
  state.
- Existing tests continue to guard against accidentally opening an unauthenticated
  HTTP endpoint.

### Negative / costs

- The repo keeps some code that is not part of the normal local-first runtime.
- Maintainers must keep remote-auth tests compatible with dependency upgrades even
  while public remote MCP remains on hold.

### Neutral / unchanged

- stdio remains the default and the normal operating mode.
- HTTP remains explicit opt-in and fail-closed.
- Viewer-side Supabase Auth (#108) is unchanged and remains in scope for the
  read-only viewer.
- The project remains single-user/single-tenant; no RLS, tenant IDs, or
  multi-tenant isolation are introduced.

## Implementation references

- `src/ai_books/server.py` — dormant HTTP transport selection and fail-closed launch guard.
- `src/ai_books/auth.py` — dormant Supabase JWT + single-user allowlist provider.
- `README.md` / `AGENTS.md` — current local-first/dormant remote posture.
- `docs/remote/deploy-runbook.md` — retained as a restart reference, not a daily
  operating procedure.
- `tests/test_server_http.py` — HTTP startup, unauthenticated 401, and auth-required guard.
- `tests/test_auth.py` — allowlist and provider construction unit coverage.
- `tests/test_auth_audit_db.py` — DB-backed authenticated audit actor coverage.

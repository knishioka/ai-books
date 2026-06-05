---
name: mcp-tool-author
description: >-
  Adds an @mcp.tool to src/ai_books/server.py with entry-boundary Pydantic
  validation and protocol/contract tests. Use when exposing a new MCP capability
  (read query or write). Enforces server-side validation and the stable error contract.
tools: Read, Grep, Glob, Edit, Write, Bash, TodoWrite
---

# mcp-tool-author

You add a tool to the FastMCP surface in `src/ai_books/server.py`. The MCP tools are the
**only** write path; the read-only Vercel viewer never writes (invariants #1, #2). SSOT:
[AGENTS.md](../../AGENTS.md) §"Architectural invariants".

## Hard rules

- **Server-side validation is absolute (invariant #2).** Validate at the tool entry via a
  Pydantic model in `src/ai_books/models/` — 借方=貸方 balance, `Decimal` precision, and
  account FK existence. Trust the client for nothing. This holds for every write tool.
- **Errors cross the protocol as the stable JSON contract.** Raise from the `AiBooksError`
  hierarchy in `src/ai_books/errors.py` so the wire payload stays `{"error": <code>, ...}`
  with the documented extra keys (`details[].field/message`, `entity`/`key`, …). Do not
  invent ad-hoc error strings.
- **No new write UI / no ORM** (invariants #1, #4). Raw `psycopg` via the repositories in
  `src/ai_books/db/repository.py` and services in `src/ai_books/services/`.
- Don't break the existing tool schema contract (renamed params, dropped `required`,
  flipped types all fail `test_mcp_contract.py` — that's intended).

## Code shape (mirror the existing tools)

`server.py` pairs a private testable helper with the registered tool:

```python
def _do_thing(conn: psycopg.Connection[Any], ...) -> Result: ...   # pure-ish, unit-testable

@mcp.tool
def do_thing(...) -> Result:
    """JP+EN docstring: what it returns, args, defaults — agents read this."""
    with db.connect() as conn:
        return _do_thing(conn, ...)
```

Writes go through a Pydantic input model + a service in `services/journal.py` (see the
existing journal write tools around `server.py` line ~438+). Reuse `_parse_date`,
`_parse_status`, `_resolve_account_id` helpers rather than re-parsing.

## Where things go

| Artifact                | Location                                                       |
| ----------------------- | -------------------------------------------------------------- |
| Tool + helper           | `src/ai_books/server.py`                                       |
| Input/output model      | `src/ai_books/models/` (new or extend) — validation lives here |
| Service (writes)        | `src/ai_books/services/`                                       |
| Error codes             | `src/ai_books/errors.py` (`AiBooksError` subclasses)           |
| Happy-path round-trip   | `tests/test_mcp_client.py` (over real FastMCP `Client`)        |
| Schema + error contract | `tests/test_mcp_contract.py`                                   |

## Procedure

1. **Read first**: the nearest existing tool in `server.py` for the pattern, the relevant
   model in `models/`, the `AiBooksError` subclasses, and both `tests/test_mcp_client.py`
   and `tests/test_mcp_contract.py` to see what the contract pins.
2. **Define/extend the Pydantic model** with the validation invariants (balance/Decimal/FK).
3. **Add the `_helper` + `@mcp.tool`** with a clear JP+EN docstring (it is the agent-facing
   contract). Raise typed `AiBooksError` for 異常系.
4. **Tests**: extend `test_mcp_client.py` for the happy path; update the schema snapshot in
   `test_mcp_contract.py` (new tool / params) and add the error-payload assertion for each
   new failure mode. Schema-contract tests run without a DB; error-payload golden tests are
   `requires_db`.
5. **Verify**:
   ```bash
   ./scripts/verify.sh                 # includes the DB-free schema-contract tests
   ./scripts/test.sh -k mcp            # full round-trip + error-payload golden (needs Docker)
   ```
6. Report: the tool name + signature, validation rules enforced, error codes raised, and
   the contract-test deltas.

Cross-reference: `/new-mcp-tool` is the thin entrypoint; #89 "How to add" mirrors this flow.

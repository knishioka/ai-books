---
description: Scaffold an @mcp.tool with entry-boundary Pydantic validation + protocol/contract tests.
argument-hint: "<tool_name>"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Task
---

Scaffold a new MCP tool named `$ARGUMENTS`.

Delegate to the **mcp-tool-author** subagent. Pass the requested name and these
non-negotiables (SSOT: [AGENTS.md](../../AGENTS.md) invariants #2, #4):

- Add `@mcp.tool $ARGUMENTS` (+ a private `_helper`) in `src/ai_books/server.py`, mirroring
  the existing tools, with a JP+EN docstring (the agent-facing contract).
- **Server-side validation at the entry** via a Pydantic model in `src/ai_books/models/` —
  借方=貸方 balance, `Decimal` precision, account FK. Trust the client for nothing. Writes go
  through `src/ai_books/services/`.
- Raise typed errors from `src/ai_books/errors.py` (`AiBooksError`) so the wire payload stays
  the stable `{"error": <code>, …}` contract.
- Tests: happy-path round-trip in `tests/test_mcp_client.py`; schema + error-payload contract
  in `tests/test_mcp_contract.py`.

Verify before reporting:

```bash
./scripts/verify.sh           # includes DB-free schema-contract tests
./scripts/test.sh -k mcp      # full round-trip + error-payload golden
```

See #89 "How to add" for the same flow. Report the tool signature, validation enforced,
error codes, and contract-test deltas.

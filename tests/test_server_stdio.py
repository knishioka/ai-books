"""Stdio boot smoke test (Issue #51).

Spawns the server exactly as a real client would — ``python -m ai_books.server``
in a subprocess speaking MCP over stdio — and drives it through the FastMCP stdio
client: initialize handshake → ``list_tools`` → a protocol-level ``hello`` call.

This is the minimum guarantee that ``main()`` actually boots: it catches import
errors, transport misconfiguration, or a broken stdio entry point that the
in-process tool-registration tests (``test_smoke.py``) cannot see, since those
never run ``mcp.run()``.

DB-independent by design — only ``hello`` is exercised over the wire (DB tools are
covered by #50), so this runs on every ``./scripts/verify.sh`` without a live
Postgres. Every interaction is wrapped in an :func:`asyncio.timeout` and the
client context manager tears the subprocess down on exit, so a hung server fails
the test instead of stalling the suite.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

# Repo root: parent of tests/. Used as the subprocess cwd so ``-m ai_books.server``
# resolves the installed package regardless of where pytest is invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Hard ceiling on any single boot+interaction. Generous enough for a cold import
# under CI, tight enough that a genuine hang surfaces as a failure.
_TIMEOUT_S = 60.0

# The full tool surface the server is expected to expose over the protocol. Kept
# as the authoritative set so a tool silently dropping out of registration (e.g. a
# decorator lost in a refactor) is caught at the boundary a real client sees.
EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "hello",
        # chart of accounts (#12)
        "list_accounts",
        "get_account",
        "search_accounts",
        # journals / balances / ledger (#15)
        "list_journal_entries",
        "get_journal_entry",
        "get_account_balance",
        "get_account_ledger",
        # aggregation (#18)
        "trial_balance",
        "monthly_trend",
        "worksheet",
        # 決算書 (#20 / #21)
        "profit_and_loss",
        "balance_sheet",
        # ledger reports (#19)
        "journal_book",
        "general_ledger",
        # e-Tax export (#24)
        "export_etax",
        # journal writes (#13)
        "create_journal_entry",
        "update_journal_entry",
        "post_journal_entry",
        "void_journal_entry",
        # CSV import (#14)
        "import_transactions_csv",
    }
)


def _stdio_client() -> Client[StdioTransport]:
    """A FastMCP client wired to a fresh ``python -m ai_books.server`` stdio subprocess.

    ``keep_alive=False`` so each ``async with`` owns its own process and the
    subprocess is terminated on context exit (no lingering server between tests).
    """
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ai_books.server"],
        cwd=str(_REPO_ROOT),
        keep_alive=False,
    )
    return Client(transport, init_timeout=_TIMEOUT_S, timeout=_TIMEOUT_S)


async def test_stdio_boot_initialize_and_list_tools() -> None:
    """Server boots over stdio, the initialize handshake completes, and tools list."""
    client = _stdio_client()
    async with asyncio.timeout(_TIMEOUT_S), client:
        assert client.is_connected()  # initialize handshake succeeded
        tools = await client.list_tools()

    names = {tool.name for tool in tools}
    # Every tool the server promises must be present in the live listing.
    assert names >= EXPECTED_TOOLS, f"missing tools: {sorted(EXPECTED_TOOLS - names)}"


async def test_stdio_hello_tool_roundtrip() -> None:
    """A DB-independent tool answers correctly over the protocol (not just in-process)."""
    client = _stdio_client()
    async with asyncio.timeout(_TIMEOUT_S), client:
        result = await client.call_tool("hello", {"name": "stdio"})

    assert not result.is_error
    assert result.data == "Hello, stdio! ai-books server is alive."


async def test_stdio_subprocess_is_torn_down() -> None:
    """The client context manager terminates the server subprocess on exit (no hang)."""
    client = _stdio_client()
    async with asyncio.timeout(_TIMEOUT_S), client:
        assert client.is_connected()
    # Leaving the context must close the session and reap the subprocess.
    assert not client.is_connected()

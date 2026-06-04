"""Shared pytest fixtures for DB-backed tests.

The ``migrated_conn`` fixture mirrors ``test_migrate.py``: it runs every test in a
throwaway schema with all migrations applied, so the suite is repeatable and leaves
nothing behind in ``public``. DB-backed test modules guard themselves with a
module-level ``skipif`` on ``AI_BOOKS_DB_URL`` so ``./scripts/verify.sh`` stays green
without a live Postgres.

The ``mcp_client`` fixture is the shared harness for exercising tools over the real
FastMCP protocol path (Client → ``call_tool`` → argument-schema coercion → execution →
result serialisation → ``ToolError``), rather than calling the Python tool functions
directly. It needs no DB itself; pair it with a fixture that points ``db.connect`` at
the throwaway schema (see ``test_mcp_client.py``) for the DB-backed tools.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import Client
from hypothesis import HealthCheck, settings
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from ai_books import db, server
from ai_books.db import migrate

DB_URL_ENV = db.DB_URL_ENV

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
_TEST_SCHEMA = "ai_books_layer_test"

# Property-based tests (Issue #57) must be deterministically reproducible (AC: "hypothesis の例が
# 決定的に再現可能"). ``derandomize=True`` seeds Hypothesis from the test source instead of the clock, so
# the same examples are explored every run (in CI and locally) — a failure is reproducible from the
# printed example, and a green run today stays green tomorrow without a flaky reseed. The DB-backed
# property tests create a throwaway schema *inside* the example (not via a function-scoped fixture), so
# the function_scoped_fixture health check would never fire for them; it is suppressed for clarity.
settings.register_profile(
    "ai_books",
    derandomize=True,
    max_examples=150,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("ai_books")


@pytest.fixture
async def mcp_client() -> AsyncIterator[Client[Any]]:
    """A connected in-memory FastMCP ``Client`` bound to the real ``server.mcp``.

    ``async with Client(server.mcp)`` speaks the actual MCP protocol in-process (no
    sockets), so tool calls go through argument-schema coercion, execution, result
    serialisation, and ``ToolError`` translation exactly as a real AI client would.
    """
    async with Client(server.mcp) as client:
        yield client


@pytest.fixture
def migrated_conn() -> Iterator[psycopg.Connection[DictRow]]:
    """A dict-row connection on a fresh, fully-migrated throwaway schema."""
    connection: psycopg.Connection[Any] = psycopg.connect(
        db.get_db_url(), autocommit=True, row_factory=dict_row
    )
    drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(_TEST_SCHEMA))
    try:
        connection.execute(drop)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(_TEST_SCHEMA)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(_TEST_SCHEMA)))
        migrate.apply_pending(connection, MIGRATIONS_DIR)
        yield connection
    finally:
        connection.execute(drop)
        connection.close()


@pytest.fixture
def patched_connect(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: psycopg.Connection[Any]
) -> None:
    """Point ``db.connect`` (opened inside every tool) at the throwaway test schema.

    The protocol-path tools (``test_mcp_client`` / ``test_mcp_contract``) each open their own
    short-lived connection rather than reusing ``migrated_conn``; this redirects those opens at
    the migrated throwaway schema so they see the seeded rows. ``migrated_conn`` is autocommit, so
    rows seeded through it are visible to the fresh connections the tools open — including across
    the worker thread FastMCP uses to run sync tools. ``monkeypatch`` auto-reverts after the test.
    """

    def _connect(db_url: str | None = None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(db.get_db_url(), row_factory=dict_row)
        conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        return conn

    monkeypatch.setattr(db, "connect", _connect)

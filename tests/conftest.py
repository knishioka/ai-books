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
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from ai_books import db, server
from ai_books.db import migrate

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
_TEST_SCHEMA = "ai_books_layer_test"


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

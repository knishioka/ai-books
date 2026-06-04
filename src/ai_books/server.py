"""FastMCP server entry point.

Registers the read-only chart-of-accounts tools (``list_accounts`` / ``get_account``
/ ``search_accounts``) over the seeded master data, plus the ``hello`` smoke test.
Write tools (journal / reports) land in later issues.

Each tool's logic lives in a plain ``_…`` helper that takes an open connection, so it
is unit-testable against the throwaway-schema fixture without going through FastMCP
dispatch; the ``@mcp.tool`` wrapper just owns the connection lifecycle.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastmcp import FastMCP

from ai_books import db
from ai_books.db.repository import AccountRepository
from ai_books.errors import RecordNotFoundError
from ai_books.models import Account, AccountType, StatementCategory

mcp: FastMCP = FastMCP(
    name="ai-books",
    instructions=(
        "AI-first accounting MCP server. Provides double-entry bookkeeping primitives "
        "(chart of accounts, journal entries, trial balance, financial statements). "
        "Read tools over the chart of accounts: list_accounts / get_account / "
        "search_accounts."
    ),
)


def _greet(name: str) -> str:
    """Pure greeting helper. Kept separate from the MCP tool wrapper so unit tests
    can exercise the logic without going through FastMCP dispatch."""
    return f"Hello, {name}! ai-books server is alive."


@mcp.tool
def hello(name: str = "world") -> str:
    """Return a greeting. M0 smoke-test tool only."""
    return _greet(name)


def _list_accounts(
    conn: psycopg.Connection[Any],
    *,
    account_type: AccountType | None = None,
    statement_category: StatementCategory | None = None,
    is_active: bool | None = None,
) -> list[Account]:
    return AccountRepository(conn).find(
        account_type=account_type,
        statement_category=statement_category,
        is_active=is_active,
    )


def _get_account(conn: psycopg.Connection[Any], code: str) -> Account:
    account = AccountRepository(conn).get_by_code(code)
    if account is None:
        raise RecordNotFoundError("account", code)
    return account


def _search_accounts(
    conn: psycopg.Connection[Any], query: str, *, include_inactive: bool = False
) -> list[Account]:
    return AccountRepository(conn).search(query, include_inactive=include_inactive)


@mcp.tool
def list_accounts(
    account_type: AccountType | None = None,
    statement_category: StatementCategory | None = None,
    is_active: bool | None = None,
) -> list[Account]:
    """List chart-of-accounts entries, optionally filtered by 区分 / 表示区分 / 有効.

    All filters are optional and combined with AND. Returns typed ``Account`` rows
    ordered by 勘定科目コード.
    """
    with db.connect() as conn:
        return _list_accounts(
            conn,
            account_type=account_type,
            statement_category=statement_category,
            is_active=is_active,
        )


@mcp.tool
def get_account(code: str) -> Account:
    """Fetch one account by its 勘定科目コード. Errors if no such account exists."""
    with db.connect() as conn:
        return _get_account(conn, code)


@mcp.tool
def search_accounts(query: str, include_inactive: bool = False) -> list[Account]:
    """Search accounts by 勘定科目コード or 科目名 substring (case-insensitive).

    Active accounts only unless ``include_inactive`` is true. Ordered by code.
    """
    with db.connect() as conn:
        return _search_accounts(conn, query, include_inactive=include_inactive)


def main() -> None:
    """Run the MCP server over stdio (FastMCP default transport)."""
    mcp.run()


if __name__ == "__main__":
    main()

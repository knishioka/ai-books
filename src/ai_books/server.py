"""FastMCP server entry point.

Registers the read-only query surface:

- chart-of-accounts tools (``list_accounts`` / ``get_account`` / ``search_accounts``)
  over the seeded master data (Issue #12);
- journal / balance / ledger tools (``list_journal_entries`` / ``get_journal_entry``
  / ``get_account_balance`` / ``get_account_ledger``) — the shared read API that
  aggregation (#18) and the Vercel viewer (#16) reuse (Issue #15).

Plus the ``hello`` smoke test. Write tools (#13) land separately.

The account tools keep their logic in plain ``_…`` helpers that take an open
connection (unit-testable against the throwaway-schema fixture without going
through FastMCP dispatch); the journal/balance/ledger tools open a short-lived
connection and delegate to the repository layer. Either way amounts stay
``Decimal`` (serialised as a string, never a float).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from fastmcp import FastMCP

from ai_books import db
from ai_books.db.repository import (
    AccountRepository,
    JournalRepository,
    LedgerRepository,
)
from ai_books.errors import RecordNotFoundError
from ai_books.models import (
    Account,
    AccountBalance,
    AccountLedger,
    AccountType,
    EntryStatus,
    JournalEntry,
    JournalEntryPage,
    StatementCategory,
)

mcp: FastMCP = FastMCP(
    name="ai-books",
    instructions=(
        "AI-first accounting MCP server. Provides double-entry bookkeeping primitives "
        "(chart of accounts, journal entries, trial balance, financial statements). "
        "Read tools cover the chart of accounts (list_accounts / get_account / "
        "search_accounts) and the journals, balances, and general ledger (総勘定元帳) — "
        "list_journal_entries / get_journal_entry / get_account_balance / "
        "get_account_ledger. Amounts are exact decimals returned as strings."
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


# --- chart of accounts (Issue #12) --------------------------------------------


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


# --- journals / balances / ledger (Issue #15) ---------------------------------


def _parse_date(value: str | None, field: str) -> date | None:
    """Parse an optional ISO ``YYYY-MM-DD`` string, raising a clear error on bad input."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD); got {value!r}") from exc


def _parse_status(value: str | None) -> EntryStatus | None:
    """Parse an optional entry status, raising a clear error listing valid values."""
    if value is None:
        return None
    try:
        return EntryStatus(value)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in EntryStatus)
        raise ValueError(f"status must be one of: {allowed}; got {value!r}") from exc


def _resolve_account_id(conn: psycopg.Connection[Any], code: str) -> int:
    """Resolve a 勘定科目コード to its id, raising :class:`RecordNotFoundError` if unknown."""
    account = AccountRepository(conn).get_by_code(code)
    if account is None or account.id is None:
        raise RecordNotFoundError("account", code)
    return account.id


@mcp.tool
def list_journal_entries(
    start_date: str | None = None,
    end_date: str | None = None,
    account_code: str | None = None,
    status: str | None = None,
    text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JournalEntryPage:
    """List journal entries (仕訳), newest first, with paging and a total match count.

    All filters are optional and combine: ``start_date``/``end_date`` (ISO, inclusive)
    bound the 取引日; ``account_code`` keeps only entries touching that account;
    ``status`` is ``draft`` or ``posted``; ``text`` is a case-insensitive substring
    matched against the entry or any line 摘要. ``limit`` is capped server-side.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code) if account_code else None
        return JournalRepository(conn).list_entries(
            start_date=_parse_date(start_date, "start_date"),
            end_date=_parse_date(end_date, "end_date"),
            account_id=account_id,
            status=_parse_status(status),
            text=text,
            limit=limit,
            offset=offset,
        )


@mcp.tool
def get_journal_entry(entry_id: int) -> JournalEntry:
    """Fetch a single journal entry with its lines attached (errors if absent)."""
    with db.connect() as conn:
        entry = JournalRepository(conn).get_entry(entry_id)
    if entry is None:
        raise RecordNotFoundError("journal_entry", entry_id)
    return entry


@mcp.tool
def get_account_balance(
    account_code: str,
    as_of: str | None = None,
    status: str | None = None,
) -> AccountBalance:
    """Return an account's balance as of ``as_of`` (ISO date, inclusive; default all time).

    ``balance`` is signed into the account's normal direction, so it is positive when
    the account carries its normal balance. Pass ``status='posted'`` to count only the
    confirmed books (記帳確定); the default includes drafts.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code)
        return LedgerRepository(conn).account_balance(
            account_id,
            as_of=_parse_date(as_of, "as_of"),
            status=_parse_status(status),
        )


@mcp.tool
def get_account_ledger(
    account_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> AccountLedger:
    """Return the 総勘定元帳 for an account over ``[start_date, end_date]`` (ISO, inclusive).

    Rows are chronological with a running balance; ``opening_balance`` is the 繰越 from
    before ``start_date`` and each row lists its 相手科目 (counter accounts). Pass
    ``status='posted'`` to restrict to the confirmed books.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code)
        return LedgerRepository(conn).account_ledger(
            account_id,
            start=_parse_date(start_date, "start_date"),
            end=_parse_date(end_date, "end_date"),
            status=_parse_status(status),
        )


def main() -> None:
    """Run the MCP server over stdio (FastMCP default transport)."""
    mcp.run()


if __name__ == "__main__":
    main()

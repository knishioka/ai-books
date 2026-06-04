"""Protocol-path MCP tool tests over an in-memory FastMCP ``Client`` (Issue #50).

The rest of the suite exercises the tools by calling their Python functions directly
(``server.get_account_balance(...)``). That proves the *logic* but skips the FastMCP
protocol path an AI client actually travels: ``Client`` → ``call_tool`` → argument
coercion against the input schema → execution → result serialisation → ``ToolError``.
These tests close that gap with the shared ``mcp_client`` harness (see ``conftest``).

The contract being pinned:

- a representative read tool (``get_account_balance``), a string-argument tool
  (``list_journal_entries`` with ``status`` / dates), a structured-result tool
  (``trial_balance`` / ``profit_and_loss``), and the write path
  (``create_journal_entry``, JSON in → Pydantic restore → JSON result) all round-trip
  over the protocol;
- string arguments (ISO dates, ``status``) coerce correctly at the protocol boundary,
  and a malformed one surfaces as a ``ToolError`` rather than a raw exception;
- the protocol result equals the direct-call result (``structured_content`` ==
  ``model_dump(mode="json")``) — so this is a *path* check, not a second implementation.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without
a live Postgres); runs under ``./scripts/test.sh``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError
from psycopg.rows import dict_row

from ai_books import db, server
from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.models import (
    Account,
    AccountType,
    EntrySide,
    EntryStatus,
    JournalEntry,
    JournalLine,
    NormalSide,
    StatementCategory,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping protocol-path MCP client tests",
)

_TEST_SCHEMA = "ai_books_layer_test"


def _structured(result: CallToolResult) -> dict[str, Any]:
    """Return a tool result's structured payload, asserting it is present.

    Every tool here declares an output schema, so FastMCP always populates
    ``structured_content``; the assert just narrows the ``dict | None`` type for indexing.
    """
    payload = result.structured_content
    assert payload is not None
    return payload


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def patched_connect(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: psycopg.Connection[Any]
) -> None:
    """Point ``db.connect`` (opened inside every tool) at the throwaway test schema.

    ``migrated_conn`` is autocommit, so rows seeded through it are visible to the fresh
    connections the tools open here — including across the worker thread FastMCP uses to
    run sync tools. ``monkeypatch`` auto-reverts after the test.
    """

    def _connect(db_url: str | None = None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(db.get_db_url(), row_factory=dict_row)
        conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        return conn

    monkeypatch.setattr(db, "connect", _connect)


class _Seed:
    """Account ids for the seeded chart, so tests can reference them by name."""

    def __init__(self, cash: int, sales: int, expense: int) -> None:
        self.cash = cash
        self.sales = sales
        self.expense = expense


def _entry(debit_id: int, credit_id: int, amount: str, entry_date: date) -> JournalEntry:
    value = Decimal(amount)
    return JournalEntry(
        entry_date=entry_date,
        status=EntryStatus.POSTED,
        lines=[
            JournalLine(account_id=debit_id, side=EntrySide.DEBIT, amount=value),
            JournalLine(account_id=credit_id, side=EntrySide.CREDIT, amount=value),
        ],
    )


@pytest.fixture
def seed(migrated_conn: psycopg.Connection[Any]) -> _Seed:
    """Seed a small but complete book: 現金 / 売上 / 消耗品費, a fiscal year, and entries.

    現金(1110, 借方正常): +1000 (4/1) -300 (5/1) → 700. 売上(4000, 貸方正常): +1000.
    消耗品費(5000, 借方正常): +300. FY2026 covers all of them so ``profit_and_loss`` and the
    period-bounded tools have a fiscal year to resolve.
    """
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.CURRENT_ASSETS,
        )
    )
    sales = accounts.insert(
        Account(
            code="4000",
            name="売上高",
            account_type=AccountType.REVENUE,
            normal_balance=NormalSide.CREDIT,
            statement_category=StatementCategory.SALES,
        )
    )
    expense = accounts.insert(
        Account(
            code="5000",
            name="消耗品費",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.SELLING_ADMIN_EXPENSES,
        )
    )
    assert cash.id is not None
    assert sales.id is not None
    assert expense.id is not None

    migrated_conn.execute(
        "INSERT INTO fiscal_years (name, start_date, end_date) VALUES (%s, %s, %s)",
        ("FY2026", date(2026, 1, 1), date(2026, 12, 31)),
    )

    journals = JournalRepository(migrated_conn)
    journals.insert_entry(_entry(cash.id, sales.id, "1000", date(2026, 4, 1)))
    journals.insert_entry(_entry(expense.id, cash.id, "300", date(2026, 5, 1)))
    return _Seed(cash.id, sales.id, expense.id)


# --- read tools over the protocol ---------------------------------------------


async def test_get_account_balance_over_protocol_matches_direct(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # String ``as_of`` is coerced at the boundary; the protocol result must match the
    # direct call byte-for-byte (same path, not a second implementation).
    result = await mcp_client.call_tool(
        "get_account_balance", {"account_code": "1110", "as_of": "2026-12-31"}
    )
    direct = server.get_account_balance(account_code="1110", as_of="2026-12-31")

    structured = _structured(result)
    assert structured == direct.model_dump(mode="json")
    # 現金: +1000 (4/1) -300 (5/1) = 700, signed into 借方正常, serialised as a string.
    assert structured["balance"] == "700.00"
    assert isinstance(structured["debit_total"], str)
    # The typed view exposes the same value through ``.data``.
    assert result.data.balance == "700.00"


async def test_list_journal_entries_string_args_over_protocol(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # ``status`` and the dates arrive as JSON strings and are coerced past the boundary.
    result = await mcp_client.call_tool(
        "list_journal_entries",
        {"start_date": "2026-04-01", "end_date": "2026-04-30", "status": "posted"},
    )
    direct = server.list_journal_entries(
        start_date="2026-04-01", end_date="2026-04-30", status="posted"
    )

    structured = _structured(result)
    assert structured == direct.model_dump(mode="json")
    # Only the 4/1 entry falls in April; the 5/1 entry is excluded.
    assert structured["total"] == 1
    assert structured["entries"][0]["entry_date"] == "2026-04-01"


async def test_trial_balance_structured_result_round_trips(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    result = await mcp_client.call_tool("trial_balance", {"as_of": "2026-12-31"})
    direct = server.trial_balance(as_of="2026-12-31")

    structured = _structured(result)
    assert structured == direct.model_dump(mode="json")
    # 借貸平均: footings equal exactly, and amounts cross the boundary as strings.
    assert structured["total_debit"] == structured["total_credit"]
    assert {row["code"] for row in structured["rows"]} == {"1110", "4000", "5000"}


async def test_profit_and_loss_structured_result_round_trips(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    result = await mcp_client.call_tool("profit_and_loss", {"fiscal_year": "FY2026"})
    direct = server.profit_and_loss(fiscal_year="FY2026")

    structured = _structured(result)
    assert structured == direct.model_dump(mode="json")
    assert structured["fiscal_year"] == "FY2026"


async def test_unknown_fiscal_year_raises_tool_error(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("profit_and_loss", {"fiscal_year": "FY1999"})


async def test_bad_date_argument_raises_tool_error(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # A malformed string surfaces as a protocol ``ToolError``, not a raw ValueError.
    with pytest.raises(ToolError):
        await mcp_client.call_tool(
            "get_account_balance", {"account_code": "1110", "as_of": "not-a-date"}
        )


# --- write tool over the protocol ---------------------------------------------


async def test_create_journal_entry_over_protocol(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # JSON in → Pydantic ``JournalEntryInput`` restore → execution → JSON result.
    entry = {
        "entry_date": "2026-06-01",
        "description": "protocol作成",
        "lines": [
            {"account_code": "1110", "side": "debit", "amount": "1500.00"},
            {"account_code": "4000", "side": "credit", "amount": "1500.00"},
        ],
    }
    result = await mcp_client.call_tool("create_journal_entry", {"entry": entry})
    created = _structured(result)

    assert created["status"] == "draft"
    assert created["voucher_no"] is not None
    assert created["description"] == "protocol作成"
    # Decimal precision survives the boundary as a string, never a float.
    assert [line["amount"] for line in created["lines"]] == ["1500.00", "1500.00"]

    # The write is visible to a follow-up read over the same protocol path.
    fetched = _structured(
        await mcp_client.call_tool("get_journal_entry", {"entry_id": created["id"]})
    )
    assert fetched["id"] == created["id"]
    assert len(fetched["lines"]) == 2


async def test_create_journal_entry_imbalance_is_tool_error_with_payload(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    imbalanced = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "1110", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "90.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": imbalanced})
    # The domain failure crosses the boundary as a machine-readable JSON payload.
    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "validation_error"


async def test_create_journal_entry_unknown_account_is_tool_error(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    bad = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "0000", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "100.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": bad})
    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "not_found"

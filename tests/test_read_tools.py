"""DB-backed tests for the read-side query layer and MCP tools (Issue #15).

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green
without a live Postgres); runs in CI. Covers the acceptance criteria that need a
real round-trip:

- list journal entries filtered by period / account, with paging over N fixtures;
- ``get_account_balance`` signed correctly for debit- and credit-normal accounts;
- ``get_account_ledger`` returning the per-account time series (opening balance,
  running balance, 相手科目) #19 builds on;
- the MCP tool wrappers wiring code → connection → typed result.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from ai_books import db, server
from ai_books.db.repository import AccountRepository, JournalRepository, LedgerRepository
from ai_books.errors import RecordNotFoundError
from ai_books.models import (
    Account,
    AccountType,
    EntrySide,
    EntryStatus,
    JournalEntry,
    JournalLine,
    NormalSide,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed read-tool tests",
)

_TEST_SCHEMA = "ai_books_layer_test"


# --- fixtures / seed helpers --------------------------------------------------


def _cash() -> Account:
    return Account(
        code="1110", name="現金", account_type=AccountType.ASSET, normal_balance=NormalSide.DEBIT
    )


def _sales() -> Account:
    return Account(
        code="4000",
        name="売上高",
        account_type=AccountType.REVENUE,
        normal_balance=NormalSide.CREDIT,
    )


def _expense() -> Account:
    return Account(
        code="5000",
        name="消耗品費",
        account_type=AccountType.EXPENSE,
        normal_balance=NormalSide.DEBIT,
    )


def _entry(
    debit_id: int,
    credit_id: int,
    amount: str,
    entry_date: date,
    *,
    status: EntryStatus = EntryStatus.POSTED,
    description: str | None = None,
) -> JournalEntry:
    value = Decimal(amount)
    return JournalEntry(
        entry_date=entry_date,
        status=status,
        description=description,
        lines=[
            JournalLine(account_id=debit_id, side=EntrySide.DEBIT, amount=value),
            JournalLine(account_id=credit_id, side=EntrySide.CREDIT, amount=value),
        ],
    )


class _Seed:
    """Account ids for the seeded chart, so tests can reference them by name."""

    def __init__(self, cash: int, sales: int, expense: int) -> None:
        self.cash = cash
        self.sales = sales
        self.expense = expense


@pytest.fixture
def seed(migrated_conn: psycopg.Connection[Any]) -> _Seed:
    """Insert the three accounts and a handful of posted entries; return their ids.

    Cash (asset/借方正常) movement: +1000 (4/1) +500 (4/10) -300 (5/1) → 1200.
    Sales (revenue/貸方正常): +1500. Expense (費用/借方正常): +300.
    """
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(_cash())
    sales = accounts.insert(_sales())
    expense = accounts.insert(_expense())
    assert cash.id is not None
    assert sales.id is not None
    assert expense.id is not None

    journals = JournalRepository(migrated_conn)
    journals.insert_entry(_entry(cash.id, sales.id, "1000", date(2026, 4, 1), description="売上A"))
    journals.insert_entry(_entry(cash.id, sales.id, "500", date(2026, 4, 10), description="売上B"))
    journals.insert_entry(
        _entry(expense.id, cash.id, "300", date(2026, 5, 1), description="文具購入")
    )
    return _Seed(cash.id, sales.id, expense.id)


# --- list_journal_entries -----------------------------------------------------


def test_list_entries_filters_by_period_and_account(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    repo = JournalRepository(migrated_conn)

    page = repo.list_entries(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
    assert page.total == 2  # 4/1 and 4/10, not the 5/1 entry
    assert {e.entry_date for e in page.entries} == {date(2026, 4, 1), date(2026, 4, 10)}
    # Lines come back attached.
    assert all(len(e.lines) == 2 for e in page.entries)

    by_expense = repo.list_entries(account_id=seed.expense)
    assert by_expense.total == 1
    assert by_expense.entries[0].entry_date == date(2026, 5, 1)


def test_list_entries_text_and_status_filters(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    repo = JournalRepository(migrated_conn)

    assert repo.list_entries(text="売上").total == 2
    assert repo.list_entries(text="文具").total == 1
    assert repo.list_entries(status=EntryStatus.POSTED).total == 3
    assert repo.list_entries(status=EntryStatus.DRAFT).total == 0


def test_list_entries_pages_over_many_rows(migrated_conn: psycopg.Connection[Any]) -> None:
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(_cash())
    sales = accounts.insert(_sales())
    assert cash.id is not None
    assert sales.id is not None
    journals = JournalRepository(migrated_conn)
    total_rows = 25
    for i in range(total_rows):
        journals.insert_entry(
            _entry(cash.id, sales.id, "100", date(2026, 6, 1) + timedelta(days=i))
        )

    first = journals.list_entries(limit=10, offset=0)
    assert first.total == total_rows
    assert len(first.entries) == 10
    assert first.has_more is True

    last = journals.list_entries(limit=10, offset=20)
    assert len(last.entries) == 5
    assert last.has_more is False

    # The limit is clamped so an oversized request cannot pull the whole table.
    clamped = journals.list_entries(limit=10_000)
    assert clamped.limit == 500


# --- get_account_balance ------------------------------------------------------


def test_balance_signed_for_debit_normal_account(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    ledger = LedgerRepository(migrated_conn)

    bal = ledger.account_balance(seed.cash, as_of=date(2026, 12, 31))
    assert bal.normal_balance is NormalSide.DEBIT
    assert bal.debit_total == Decimal("1500.00")
    assert bal.credit_total == Decimal("300.00")
    assert bal.balance == Decimal("1200.00")  # 借方 - 貸方, 正常残高方向で正

    # as_of excludes the 5/1 entry → only the two April debits.
    april = ledger.account_balance(seed.cash, as_of=date(2026, 4, 30))
    assert april.balance == Decimal("1500.00")


def test_balance_signed_for_credit_normal_account(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    ledger = LedgerRepository(migrated_conn)
    bal = ledger.account_balance(seed.sales)
    assert bal.normal_balance is NormalSide.CREDIT
    assert bal.credit_total == Decimal("1500.00")
    assert bal.balance == Decimal("1500.00")  # 貸方 - 借方, 正常残高方向で正


def test_balance_unknown_account_raises(migrated_conn: psycopg.Connection[Any]) -> None:

    with pytest.raises(RecordNotFoundError):
        LedgerRepository(migrated_conn).account_balance(999_999)


# --- get_account_ledger -------------------------------------------------------


def test_ledger_returns_running_balance_and_counter_accounts(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    ledger = LedgerRepository(migrated_conn)
    led = ledger.account_ledger(seed.cash)

    assert led.opening_balance == Decimal("0")
    assert [r.running_balance for r in led.rows] == [
        Decimal("1000.00"),
        Decimal("1500.00"),
        Decimal("1200.00"),
    ]
    assert led.closing_balance == Decimal("1200.00")
    # 相手科目: the two sales entries credit 売上(4000); the expense entry debits 消耗品費(5000).
    assert [r.counter_accounts for r in led.rows] == [["4000"], ["4000"], ["5000"]]
    assert [r.side for r in led.rows] == [EntrySide.DEBIT, EntrySide.DEBIT, EntrySide.CREDIT]


def test_ledger_opening_balance_carries_forward(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    ledger = LedgerRepository(migrated_conn)
    led = ledger.account_ledger(seed.cash, start=date(2026, 4, 15))

    # 4/1 and 4/10 fall before the window → carried in as the opening balance.
    assert led.opening_balance == Decimal("1500.00")
    assert [r.entry_date for r in led.rows] == [date(2026, 5, 1)]
    assert led.rows[0].running_balance == Decimal("1200.00")
    assert led.closing_balance == Decimal("1200.00")


# --- MCP tools (wiring) -------------------------------------------------------


@pytest.fixture
def patched_connect(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: psycopg.Connection[Any]
) -> None:
    """Point ``db.connect`` (used by the tools) at the throwaway test schema.

    ``migrated_conn`` is autocommit, so rows seeded through it are visible to the
    fresh connections the tools open here. ``monkeypatch`` auto-reverts after the test.
    """

    def _connect(db_url: str | None = None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(db.get_db_url(), row_factory=dict_row)
        conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        return conn

    monkeypatch.setattr(db, "connect", _connect)


def test_tool_list_journal_entries(patched_connect: None, seed: _Seed) -> None:
    page = server.list_journal_entries(account_code="5000")
    assert page.total == 1
    assert page.entries[0].description == "文具購入"


def test_tool_get_account_balance_serialises_decimal_as_string(
    patched_connect: None, seed: _Seed
) -> None:
    bal = server.get_account_balance(account_code="1110", as_of="2026-12-31")
    assert bal.balance == Decimal("1200.00")
    # Decimal is preserved (serialised as a string, never a float) at the MCP boundary.
    dumped = bal.model_dump(mode="json")
    assert dumped["balance"] == "1200.00"
    assert isinstance(dumped["debit_total"], str)


def test_tool_get_account_ledger(patched_connect: None, seed: _Seed) -> None:
    led = server.get_account_ledger(account_code="1110")
    assert led.closing_balance == Decimal("1200.00")
    assert len(led.rows) == 3


def test_tool_get_journal_entry_roundtrip(patched_connect: None, seed: _Seed) -> None:
    page = server.list_journal_entries(account_code="5000")
    entry_id = page.entries[0].id
    assert entry_id is not None
    fetched = server.get_journal_entry(entry_id)
    assert fetched.id == entry_id
    assert len(fetched.lines) == 2


def test_tool_bad_date_raises(patched_connect: None, seed: _Seed) -> None:
    with pytest.raises(ValueError, match="ISO date"):
        server.get_account_balance(account_code="1110", as_of="not-a-date")


# --- journal_book / general_ledger (Issue #19) --------------------------------


def test_tool_journal_book(patched_connect: None, seed: _Seed) -> None:
    book = server.journal_book()
    # Three seeded entries, oldest first, booking balanced.
    assert [e.entry_date for e in book.entries] == [
        date(2026, 4, 1),
        date(2026, 4, 10),
        date(2026, 5, 1),
    ]
    assert book.total_debit == book.total_credit == Decimal("1800.00")
    # Lines name their 勘定科目 inline; amounts serialise as strings at the boundary.
    assert book.entries[0].lines[0].account_code in {"1110", "4000"}
    assert book.model_dump(mode="json")["total_debit"] == "1800.00"


def test_tool_general_ledger_whole_book(patched_connect: None, seed: _Seed) -> None:
    ledger = server.general_ledger()
    assert [a.code for a in ledger.accounts] == ["1110", "4000", "5000"]
    cash = next(a for a in ledger.accounts if a.code == "1110")
    assert cash.closing_balance == Decimal("1200.00")
    assert [r.running_balance for r in cash.rows] == [
        Decimal("1000.00"),
        Decimal("1500.00"),
        Decimal("1200.00"),
    ]


def test_tool_general_ledger_single_account(patched_connect: None, seed: _Seed) -> None:
    ledger = server.general_ledger(account_code="5000")
    assert [a.code for a in ledger.accounts] == ["5000"]
    # 消耗品費(5000): one debit of 300 against 現金(1110).
    rows = ledger.accounts[0].rows
    assert len(rows) == 1
    assert rows[0].counter_accounts == ["1110"]
    assert rows[0].running_balance == Decimal("300.00")

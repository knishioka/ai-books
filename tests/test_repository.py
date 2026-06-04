"""DB-backed tests for the typed repository layer.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green
without a live Postgres); runs in CI. Verifies the acceptance criteria that need a
real round-trip: insert→select returns a typed model, and ``Decimal`` amounts are
preserved exactly through the database.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.models import (
    Account,
    AccountType,
    EntrySide,
    JournalEntry,
    JournalLine,
    NormalSide,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed repository tests",
)


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


def test_account_insert_select_round_trip(migrated_conn: psycopg.Connection[Any]) -> None:
    repo = AccountRepository(migrated_conn)
    stored = repo.insert(_cash())

    assert stored.id is not None  # DB assigned an identity
    assert stored.code == "1110"
    assert stored.account_type is AccountType.ASSET
    assert stored.created_at is not None

    assert repo.get(stored.id) == stored
    by_code = repo.get_by_code("1110")
    assert by_code is not None
    assert by_code.id == stored.id


def test_account_get_missing_returns_none(migrated_conn: psycopg.Connection[Any]) -> None:
    repo = AccountRepository(migrated_conn)
    assert repo.get(999_999) is None
    assert repo.get_by_code("nope") is None


def test_journal_entry_round_trip_preserves_decimal(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(_cash())
    sales = accounts.insert(_sales())
    assert cash.id is not None
    assert sales.id is not None

    amount = Decimal("1234567.89")
    entry = JournalEntry(
        entry_date=date(2026, 4, 1),
        description="売上",
        lines=[
            JournalLine(account_id=cash.id, side=EntrySide.DEBIT, amount=amount),
            JournalLine(account_id=sales.id, side=EntrySide.CREDIT, amount=amount),
        ],
    )

    repo = JournalRepository(migrated_conn)
    stored = repo.insert_entry(entry)

    assert stored.id is not None
    assert len(stored.lines) == 2
    assert stored.is_balanced
    # Decimal survives the DB round-trip with no float drift.
    for line in stored.lines:
        assert isinstance(line.amount, Decimal)
        assert line.amount == amount
    assert {line.line_no for line in stored.lines} == {1, 2}

    assert repo.get_entry(stored.id) == stored


def test_get_entry_missing_returns_none(migrated_conn: psycopg.Connection[Any]) -> None:
    repo = JournalRepository(migrated_conn)
    assert repo.get_entry(999_999) is None


def test_base_fetch_all_and_execute(migrated_conn: psycopg.Connection[Any]) -> None:
    repo = AccountRepository(migrated_conn)
    repo.insert(_cash())
    repo.insert(_sales())

    everything = repo.fetch_all("SELECT * FROM accounts ORDER BY code")
    assert [account.code for account in everything] == ["1110", "4000"]

    affected = repo.execute("UPDATE accounts SET is_active = false WHERE code = %s", ("1110",))
    assert affected == 1

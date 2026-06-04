"""Supabase pooler (pgbouncer, transaction mode) safety tests (Issue #52).

Production reaches Postgres through Supabase's pooler (pgbouncer / Supavisor in
*transaction* mode), which routes each transaction to a possibly-different backend and
therefore cannot preserve a prepared statement created on an earlier one. A direct
``postgres:17`` connection never exposes this class of failure, so these tests run the
production read / write / aggregation / ledger / e-Tax paths **through a real pgbouncer**
and assert they still succeed — and that a regression which re-enables prepared statements
is caught mechanically.

Gated on ``AI_BOOKS_POOLER_URL`` (the pgbouncer URL), so the default
``./scripts/verify.sh`` / ``./scripts/test.sh`` runs skip it: a transaction-pooling proxy
is only present under ``./scripts/test.sh --pooler`` and the CI ``pooler`` job, which set
that variable. The module seeds the synthetic FY2025 fixture (idempotently, via the
production write path) into the ``public`` schema through the pooler, so the DB-backed
reports below reproduce the committed #17 golden byte-for-byte — the same gate the viewer's
``web/scripts/verify-golden.ts`` enforces, but exercising the Python engine over the pooler.

The throwaway-schema ``migrated_conn`` fixture (tests/conftest.py) is intentionally *not*
reused here: it relies on a session-level ``SET search_path`` that a transaction-pooling
proxy cannot preserve across transactions, so the pooler suite owns its ``public``-schema
seed instead.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db import migrate
from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.etax import etax_export_snapshot
from ai_books.models import EntrySide, EntryStatus, JournalEntry, JournalLine
from ai_books.reports import (
    balance_sheet_snapshot,
    financial_statements_snapshot,
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    worksheet_snapshot,
)
from tests.fixtures.seed_fy import (
    MONTHLY_TREND_ACCOUNTS,
    balance_sheet_from_db,
    diff_snapshots,
    etax_export_from_db,
    financial_statements_from_db,
    general_ledger_from_db,
    journal_book_from_db,
    load_fiscal_year,
    load_golden,
    monthly_trend_from_db,
    monthly_trend_snapshot,
    profit_and_loss_from_db,
    trial_balance_from_db,
    trial_balance_snapshot,
    worksheet_from_db,
)

POOLER_URL_ENV = "AI_BOOKS_POOLER_URL"
_POOLER_URL = os.environ.get(POOLER_URL_ENV)
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

pytestmark = pytest.mark.skipif(
    not _POOLER_URL,
    reason=f"{POOLER_URL_ENV} not set; skipping Supabase pooler (pgbouncer) safety tests",
)


@pytest.fixture(scope="module")
def _seeded_pooler() -> str:
    """Migrate + seed FY2025 into ``public`` through the pooler, once per module.

    Idempotent: migrations already applied are skipped and the fixture loader skips
    vouchers already present, so this is safe whether or not ``scripts/test.sh --pooler``
    pre-seeded the database. Proves the migration + write path survive transaction pooling.
    """
    assert _POOLER_URL is not None  # narrowed by the module-level skipif
    # migrate.run() owns its own (tuple-row, pooler-safe) connection, matching
    # scripts/seed_verify_db.py — and unlike db.connect()'s dict_row, which the
    # migration runner's `SELECT version FROM schema_migrations` does not expect.
    migrate.run(db_url=_POOLER_URL, migrations_dir=_MIGRATIONS_DIR)
    with db.connect(_POOLER_URL) as conn:
        conn.autocommit = True
        load_fiscal_year(conn)
    return _POOLER_URL


@pytest.fixture
def pooler_conn(_seeded_pooler: str) -> Iterator[psycopg.Connection[Any]]:
    """A dict-row connection to the seeded ``public`` schema, through the pooler.

    Uses the production :func:`ai_books.db.connect`, so the connection inherits the
    prepared-statement-free configuration that makes the stack pooler-safe (#52).
    """
    conn = db.connect(_seeded_pooler)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


# --- read / aggregation / ledger / e-Tax through the pooler -----------------------
# Each production report builder must run end-to-end over pgbouncer and reproduce the
# frozen #17 golden byte-for-byte — the prepared-statement-free path returns identical
# numbers, and a divergence pins a pooler-induced storage/aggregation regression.


def test_trial_balance_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    tb = trial_balance_from_db(pooler_conn)
    assert tb.is_balanced
    assert tb.total_debit == tb.total_credit
    problems = diff_snapshots(load_golden("trial_balance"), trial_balance_snapshot(tb))
    assert problems == [], "trial balance diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_monthly_trend_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    trends = [monthly_trend_from_db(pooler_conn, code) for code in MONTHLY_TREND_ACCOUNTS]
    problems = diff_snapshots(load_golden("monthly_trend"), monthly_trend_snapshot(trends))
    assert problems == [], "monthly trend diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_journal_book_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    snapshot = journal_book_snapshot(journal_book_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("journal_book"), snapshot)
    assert problems == [], "journal book diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_general_ledger_through_pooler_matches_golden(
    pooler_conn: psycopg.Connection[Any],
) -> None:
    snapshot = general_ledger_snapshot(general_ledger_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("general_ledger"), snapshot)
    assert problems == [], "general ledger diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_profit_and_loss_through_pooler_matches_golden(
    pooler_conn: psycopg.Connection[Any],
) -> None:
    snapshot = profit_and_loss_snapshot(profit_and_loss_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("profit_and_loss"), snapshot)
    assert problems == [], "P&L diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_balance_sheet_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    snapshot = balance_sheet_snapshot(balance_sheet_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("balance_sheet"), snapshot)
    assert problems == [], "balance sheet diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_worksheet_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    snapshot = worksheet_snapshot(worksheet_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("worksheet"), snapshot)
    assert problems == [], "worksheet diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_financial_statements_through_pooler_matches_golden(
    pooler_conn: psycopg.Connection[Any],
) -> None:
    snapshot = financial_statements_snapshot(financial_statements_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("financial_statements"), snapshot)
    assert problems == [], "決算書 diverged over pooler:\n  - " + "\n  - ".join(problems)


def test_etax_export_through_pooler_matches_golden(pooler_conn: psycopg.Connection[Any]) -> None:
    snapshot = etax_export_snapshot(etax_export_from_db(pooler_conn))
    problems = diff_snapshots(load_golden("etax_export"), snapshot)
    assert problems == [], "e-Tax export diverged over pooler:\n  - " + "\n  - ".join(problems)


# --- write path through the pooler ------------------------------------------------


def test_insert_entry_round_trips_through_pooler(pooler_conn: psycopg.Connection[Any]) -> None:
    """A repository write (INSERT header + lines in one transaction) survives pooling.

    Uses a *draft* entry so it never perturbs the posted-only golden reports above, and
    deletes it afterwards regardless. The point is that the multi-statement write
    transaction commits cleanly through transaction-mode pgbouncer.
    """
    accounts = AccountRepository(pooler_conn)
    cash = accounts.get_by_code("1110")
    sales = accounts.get_by_code("4110")
    assert cash is not None
    assert cash.id is not None
    assert sales is not None
    assert sales.id is not None

    repo = JournalRepository(pooler_conn)
    entry = JournalEntry(
        entry_date=date(2025, 6, 1),
        description="pooler write smoke",
        voucher_no="POOLER-WRITE-001",
        source="test",
        status=EntryStatus.DRAFT,
        lines=[
            JournalLine(
                line_no=1, account_id=cash.id, side=EntrySide.DEBIT, amount=Decimal("1000")
            ),
            JournalLine(
                line_no=2, account_id=sales.id, side=EntrySide.CREDIT, amount=Decimal("1000")
            ),
        ],
    )
    stored = repo.insert_entry(entry)
    try:
        assert stored.id is not None
        fetched = repo.get_entry(stored.id)
        assert fetched is not None
        assert fetched.is_balanced
        assert {ln.account_id for ln in fetched.lines} == {cash.id, sales.id}
    finally:
        pooler_conn.execute("DELETE FROM journal_lines WHERE entry_id = %s", (stored.id,))
        pooler_conn.execute("DELETE FROM journal_entries WHERE id = %s", (stored.id,))


# --- regression guard: prepared statements must stay disabled over the pooler -----


def _run_repeatedly(conn: psycopg.Connection[Any], times: int = 5) -> None:
    """Run the same parameterized query ``times`` times in autocommit (separate txns)."""
    for _ in range(times):
        conn.execute("SELECT count(*) FROM accounts WHERE code = %s", ("1110",)).fetchone()


@pytest.mark.usefixtures("_seeded_pooler")
def test_prepared_statements_break_through_pooler() -> None:
    """The contrast that makes a prepared-statement regression detectable.

    With prepared statements disabled (the production default, ``prepare_threshold=None``)
    the same query repeats cleanly across transactions. Re-enable them
    (``prepare_threshold=0``) and the second transaction lands on a server whose prepared
    statement the pooler already discarded — ``prepared statement "..." does not exist``.
    So if anyone drops the pooler-safe configuration, this test goes red.
    """
    assert _POOLER_URL is not None  # narrowed by the module-level skipif

    # Positive control: prepare-disabled survives transaction pooling.
    with psycopg.connect(_POOLER_URL, autocommit=True, prepare_threshold=None) as safe:
        _run_repeatedly(safe)

    # Negative: prepare-enabled fails once the server connection is recycled.
    with (
        psycopg.connect(_POOLER_URL, autocommit=True, prepare_threshold=0) as risky,
        pytest.raises(psycopg.Error) as excinfo,
    ):
        _run_repeatedly(risky)
    assert "prepared statement" in str(excinfo.value).lower()

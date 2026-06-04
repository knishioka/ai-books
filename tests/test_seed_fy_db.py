"""DB-backed tests: load the synthetic year and check it against golden over a real DB.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without a
live Postgres); runs in CI on the throwaway-schema ``migrated_conn`` fixture. Covers the
acceptance criteria that need a round-trip: idempotent load, the DB books balancing, the
SQL-derived trial balance matching the committed golden, and the existing read API (#15)
returning the same balances downstream report Issues will reuse.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db.repository import AccountRepository, LedgerRepository
from ai_books.models import EntryStatus
from tests.fixtures.seed_fy import (
    FY_ENTRIES,
    diff_snapshots,
    load_fiscal_year,
    load_golden,
    trial_balance_from_db,
    trial_balance_snapshot,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed seed_fy tests",
)


def _entry_count(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM journal_entries")
        row = cur.fetchone()
        assert row is not None
        return int(row["n"])


def test_load_inserts_full_year(migrated_conn: psycopg.Connection[Any]) -> None:
    result = load_fiscal_year(migrated_conn)
    assert result.inserted == len(FY_ENTRIES)
    assert result.total == len(FY_ENTRIES)
    assert _entry_count(migrated_conn) == len(FY_ENTRIES)


def test_load_is_idempotent(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: seed 投入が冪等 — re-loading inserts nothing and does not duplicate.
    load_fiscal_year(migrated_conn)
    after_first = _entry_count(migrated_conn)

    second = load_fiscal_year(migrated_conn)
    assert second.inserted == 0
    assert second.skipped == len(FY_ENTRIES)
    assert _entry_count(migrated_conn) == after_first == len(FY_ENTRIES)


def test_db_books_balance_overall(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 借貸が全体でバランスする, verified against the real stored rows.
    load_fiscal_year(migrated_conn)
    trial_balance = trial_balance_from_db(migrated_conn)
    assert trial_balance.is_balanced
    assert trial_balance.total_debit == trial_balance.total_credit


def test_db_trial_balance_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: golden 比較ハーネスが pytest から動く — DB round-trip equals the frozen snapshot.
    load_fiscal_year(migrated_conn)
    actual = trial_balance_snapshot(trial_balance_from_db(migrated_conn))
    expected = load_golden("trial_balance")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB trial balance diverged from golden:\n  - " + "\n  - ".join(problems)


def test_golden_only_counts_posted_entries(migrated_conn: psycopg.Connection[Any]) -> None:
    # The harness reads 記帳確定 only; a stray draft must not perturb the snapshot.
    load_fiscal_year(migrated_conn)
    migrated_conn.execute(
        "UPDATE journal_entries SET status = 'draft' WHERE voucher_no = %s",
        ("FY2025-004",),
    )
    posted = trial_balance_from_db(migrated_conn, status=EntryStatus.POSTED)
    expected = load_golden("trial_balance")
    # 売上 (現金) FY2025-004 is now a draft, so 売上高 and 現金 drop out of the posted books.
    problems = diff_snapshots(expected, trial_balance_snapshot(posted))
    assert any("4110" in problem for problem in problems)


def test_read_api_balances_match_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 後続レポート Issue がこの seed/golden を再利用できる — the existing #15 read API
    # (get_account_balance) returns exactly the golden balances, so reports built on it agree.
    load_fiscal_year(migrated_conn)
    golden_rows = {row["code"]: row for row in load_golden("trial_balance")["rows"]}
    accounts = AccountRepository(migrated_conn)
    ledger = LedgerRepository(migrated_conn)

    for code in ("4110", "1160", "7250", "5130", "2120"):
        account = accounts.get_by_code(code)
        assert account is not None
        assert account.id is not None
        balance = ledger.account_balance(account.id, status=EntryStatus.POSTED)
        assert balance.balance == Decimal(golden_rows[code]["balance"])
        assert balance.debit_total == Decimal(golden_rows[code]["debit_total"])
        assert balance.credit_total == Decimal(golden_rows[code]["credit_total"])

"""DB-backed tests for the aggregation engine (Issue #18).

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without a
live Postgres); runs in CI on the throwaway-schema ``migrated_conn`` fixture. Covers the
acceptance criteria that need a real round-trip: the SQL-derived 合計残高試算表 always foots
(借方合計 = 貸方合計) and matches the committed golden; period-bounded trial balances; the
月次推移 partitioned by accounting month, reconciling end to end and matching golden; and
that a large fixture still aggregates in well under a second (one GROUP BY, not N queries).
"""

from __future__ import annotations

import os
import time
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db.repository import (
    AccountRepository,
    FiscalYearRepository,
    LedgerRepository,
)
from ai_books.errors import RecordNotFoundError
from ai_books.models import EntryStatus
from tests.fixtures.seed_fy import (
    FY_END,
    MONTHLY_TREND_ACCOUNTS,
    diff_snapshots,
    load_fiscal_year,
    load_golden,
    monthly_trend_from_db,
    monthly_trend_snapshot,
    trial_balance_snapshot,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed aggregation tests",
)


def _row(tb: Any, code: str) -> Any:
    """The single trial-balance row for ``code`` (fails the test if absent)."""
    rows = [r for r in tb.rows if r.code == code]
    assert rows, f"no trial-balance row for {code}"
    return rows[0]


# --- trial balance ------------------------------------------------------------


def test_trial_balance_always_foots(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 借方合計 = 貸方合計 が常に成立 — every entry is balanced, so any cut of them foots.
    load_fiscal_year(migrated_conn)
    tb = LedgerRepository(migrated_conn).trial_balance(status=EntryStatus.POSTED)
    assert tb.is_balanced
    assert tb.total_debit == tb.total_credit


def test_trial_balance_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 集計値が #17 の golden と一致 — production engine equals the frozen snapshot.
    load_fiscal_year(migrated_conn)
    tb = LedgerRepository(migrated_conn).trial_balance(status=EntryStatus.POSTED)
    problems = diff_snapshots(load_golden("trial_balance"), trial_balance_snapshot(tb))
    assert problems == [], "trial balance diverged from golden:\n  - " + "\n  - ".join(problems)


def test_trial_balance_as_of_bounds_the_period(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)
    # As of end of February only the 現金売上 (220,000, FY2025-004) has hit 売上高;
    # the March and September 掛売上 are excluded.
    tb = repo.trial_balance(as_of=date(2025, 2, 28), status=EntryStatus.POSTED)
    assert _row(tb, "4110").balance == Decimal("220000.00")
    assert tb.is_balanced  # a date cut of whole balanced entries still foots


def test_trial_balance_start_and_as_of_window(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)
    # Only March entries: 掛売上 (FY2025-005) and 買掛金支払 (FY2025-006).
    tb = repo.trial_balance(
        start=date(2025, 3, 1), as_of=date(2025, 3, 31), status=EntryStatus.POSTED
    )
    assert _row(tb, "4110").balance == Decimal("550000.00")  # 売上高 (掛, 春)
    assert _row(tb, "1160").balance == Decimal("550000.00")  # 売掛金 計上
    assert _row(tb, "2120").balance == Decimal("-600000.00")  # 買掛金 (借方支払のみ → 正常残高の逆)
    assert tb.is_balanced


def test_trial_balance_excludes_voided(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)
    before = _row(repo.trial_balance(), "4110").balance
    # Void the 現金売上; the default (no status) must drop it from the books.
    migrated_conn.execute(
        "UPDATE journal_entries SET status = 'voided', void_reason = 'test' WHERE voucher_no = %s",
        ("FY2025-004",),
    )
    after = _row(repo.trial_balance(), "4110").balance
    assert after == before - Decimal("220000.00")


# --- monthly trend ------------------------------------------------------------


def test_monthly_trend_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 集計値が #17 の golden と一致 — production 月次推移 equals the frozen snapshot.
    load_fiscal_year(migrated_conn)
    trends = [monthly_trend_from_db(migrated_conn, code) for code in MONTHLY_TREND_ACCOUNTS]
    problems = diff_snapshots(load_golden("monthly_trend"), monthly_trend_snapshot(trends))
    assert problems == [], "monthly trend diverged from golden:\n  - " + "\n  - ".join(problems)


def test_monthly_trend_partitions_into_twelve_months(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # AC: 月次推移が会計期間で正しく区切られる.
    load_fiscal_year(migrated_conn)
    trend = monthly_trend_from_db(migrated_conn, "1141")
    assert [p.month for p in trend.points] == [f"2025-{m:02d}" for m in range(1, 13)]
    assert trend.start_date == date(2025, 1, 1)
    assert trend.end_date == date(2025, 12, 31)


def test_monthly_trend_reconciles_with_account_balance(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # 期首残高 + Σ期中増減 = 期末残高, and 期末残高 equals the #15 read-layer balance as of 期末.
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)
    accounts = AccountRepository(migrated_conn)
    for code in MONTHLY_TREND_ACCOUNTS:
        trend = monthly_trend_from_db(migrated_conn, code)
        assert trend.is_consistent, f"{code}: opening + Σ net must equal closing"
        account = accounts.get_by_code(code)
        assert account is not None
        assert account.id is not None
        balance = repo.account_balance(account.id, as_of=FY_END, status=EntryStatus.POSTED)
        assert trend.closing_balance == balance.balance, f"{code}: 期末残高 != get_account_balance"


def test_monthly_trend_unknown_account_raises(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    with pytest.raises(RecordNotFoundError):
        LedgerRepository(migrated_conn).monthly_trend(
            999_999, fiscal_year="FY2025", start=date(2025, 1, 1), end=date(2025, 12, 31)
        )


def test_fiscal_year_lookup(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    repo = FiscalYearRepository(migrated_conn)
    fy = repo.get_by_name("FY2025")
    assert fy is not None
    assert (fy.start_date, fy.end_date) == (date(2025, 1, 1), date(2025, 12, 31))
    assert repo.get_by_name("FY9999") is None


# --- performance (大量仕訳) ----------------------------------------------------


def _bulk_insert_balanced_entries(
    conn: psycopg.Connection[Any], debit_id: int, credit_id: int, n: int
) -> None:
    """Insert ``n`` posted, balanced entries (¥100 debit_id→credit_id) spread across 2025.

    One round-trip via ``generate_series`` so the fixture is about *aggregation* speed,
    not insertion speed. Each entry is internally balanced, so the books stay balanced.
    """
    conn.execute(
        """
        WITH new_entries AS (
            INSERT INTO journal_entries (entry_date, description, source, status)
            SELECT DATE '2025-01-01' + ((g - 1) %% 365), 'perf', 'perf', 'posted'::entry_status
            FROM generate_series(1, %(n)s) AS g
            RETURNING id
        )
        INSERT INTO journal_lines (entry_id, line_no, account_id, side, amount)
        SELECT id, 1, %(debit)s, 'debit'::entry_side, 100 FROM new_entries
        UNION ALL
        SELECT id, 2, %(credit)s, 'credit'::entry_side, 100 FROM new_entries
        """,
        {"n": n, "debit": debit_id, "credit": credit_id},
    )


def test_trial_balance_scales_to_many_entries(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 大量仕訳でも妥当な時間で集計 (N件フィクスチャ). One GROUP BY, not N round-trips.
    load_fiscal_year(migrated_conn)
    accounts = AccountRepository(migrated_conn)
    cash = accounts.get_by_code("1110")
    sales = accounts.get_by_code("4110")
    assert cash is not None
    assert cash.id is not None
    assert sales is not None
    assert sales.id is not None

    n = 5_000
    _bulk_insert_balanced_entries(migrated_conn, cash.id, sales.id, n)

    start = time.perf_counter()
    tb = LedgerRepository(migrated_conn).trial_balance(status=EntryStatus.POSTED)
    elapsed = time.perf_counter() - start

    assert tb.is_balanced
    # The N extra ¥100 cash debits land on top of the seed's 現金 balance.
    assert _row(tb, "1110").balance == Decimal("300000.00") + Decimal(n * 100)
    assert elapsed < 2.0, f"trial balance over {n} entries took {elapsed:.3f}s"

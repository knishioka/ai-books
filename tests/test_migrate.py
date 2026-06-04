"""DB-backed tests for the forward-only migration runner.

Skips when ``AI_BOOKS_DB_URL`` is unset so ``./scripts/verify.sh`` stays green
without a live Postgres; runs in CI (and locally against a Postgres) where it is
set. Each test applies the migrations inside a throwaway schema so the suite is
repeatable and leaves nothing behind in ``public``.
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
from psycopg import sql

from ai_books import db
from ai_books.db import migrate

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
_TEST_SCHEMA = "ai_books_migrate_test"

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed migration tests",
)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    """A connection scoped to a fresh throwaway schema, dropped on teardown."""
    connection = psycopg.connect(db.get_db_url(), autocommit=True)
    drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(_TEST_SCHEMA))
    try:
        connection.execute(drop)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(_TEST_SCHEMA)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(_TEST_SCHEMA)))
        yield connection
    finally:
        connection.execute(drop)
        connection.close()


def _table_names(connection: psycopg.Connection[Any]) -> set[str]:
    cur = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (_TEST_SCHEMA,),
    )
    return {row[0] for row in cur.fetchall()}


def _seed_account(connection: psycopg.Connection[Any], code: str) -> int:
    cur = connection.execute(
        "INSERT INTO accounts (code, name, account_type, normal_balance) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (code, "現金", "asset", "debit"),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def test_applies_all_migrations_to_clean_db(conn: psycopg.Connection[Any]) -> None:
    applied = migrate.apply_pending(conn, MIGRATIONS_DIR)

    expected = [p.name for p in migrate.discover_migrations(MIGRATIONS_DIR)]
    assert applied == expected
    assert {
        "accounts",
        "journal_entries",
        "journal_lines",
        "fiscal_years",
        "periods",
        "audit_logs",
        "schema_migrations",
    } <= _table_names(conn)


def test_rerun_is_idempotent(conn: psycopg.Connection[Any]) -> None:
    first = migrate.apply_pending(conn, MIGRATIONS_DIR)
    assert first  # something was applied on a clean schema

    second = migrate.apply_pending(conn, MIGRATIONS_DIR)
    assert second == []  # nothing re-applied

    row = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
    assert row is not None
    assert row[0] == len(first)


def test_audit_logs_reject_update_and_delete(conn: psycopg.Connection[Any]) -> None:
    migrate.apply_pending(conn, MIGRATIONS_DIR)
    conn.execute("INSERT INTO audit_logs (actor, action) VALUES (%s, %s)", ("tester", "insert"))

    with pytest.raises(psycopg.Error) as update_err:
        conn.execute("UPDATE audit_logs SET actor = 'x'")
    assert "append-only" in str(update_err.value)

    with pytest.raises(psycopg.Error) as delete_err:
        conn.execute("DELETE FROM audit_logs")
    assert "append-only" in str(delete_err.value)

    row = conn.execute("SELECT count(*) FROM audit_logs").fetchone()
    assert row is not None
    assert row[0] == 1  # the original row survived both attempts


def test_amount_is_numeric_and_preserves_decimal(conn: psycopg.Connection[Any]) -> None:
    migrate.apply_pending(conn, MIGRATIONS_DIR)
    account_id = _seed_account(conn, "1110")
    entry = conn.execute(
        "INSERT INTO journal_entries (entry_date, description) VALUES (%s, %s) RETURNING id",
        (date(2026, 4, 1), "test entry"),
    ).fetchone()
    assert entry is not None

    amount = Decimal("1234567.89")
    conn.execute(
        "INSERT INTO journal_lines (entry_id, account_id, side, amount) VALUES (%s, %s, %s, %s)",
        (entry[0], account_id, "debit", amount),
    )

    row = conn.execute("SELECT amount FROM journal_lines").fetchone()
    assert row is not None
    stored = row[0]
    assert isinstance(stored, Decimal)  # NUMERIC round-trips as Decimal, not float
    assert stored == amount


def test_account_normal_balance_consistency_enforced(conn: psycopg.Connection[Any]) -> None:
    migrate.apply_pending(conn, MIGRATIONS_DIR)

    # 資産 (asset) must be debit-normal; credit violates the CHECK constraint.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO accounts (code, name, account_type, normal_balance) "
            "VALUES (%s, %s, %s, %s)",
            ("9999", "inconsistent", "asset", "credit"),
        )

    # 収益 (revenue) with credit-normal is consistent and accepted.
    conn.execute(
        "INSERT INTO accounts (code, name, account_type, normal_balance) VALUES (%s, %s, %s, %s)",
        ("4000", "売上高", "revenue", "credit"),
    )

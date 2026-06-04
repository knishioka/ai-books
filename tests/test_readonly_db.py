"""Read-only viewer role enforcement against a real Postgres (Issue #54).

The viewer is a **read-only** surface (AGENTS.md invariant #1). Until now that was only
asserted in prose ("point production at a read-only role"); nothing stopped a write query
from slipping into ``web/lib/reports/*`` or the role from silently gaining write rights.
This module makes it mechanical: it builds the *exact* read-only grant set
(``tests/fixtures/readonly.grant_statements`` — the same source the committed
``supabase/roles/viewer_readonly.sql`` renders from) on a throwaway schema, connects **as
that role**, and proves both halves of the invariant:

* every read the viewer performs succeeds — the chart of accounts plus each report
  builder, reproducing the frozen #17 golden byte-for-byte (the same numbers
  ``web/scripts/verify-golden.ts`` checks, here exercised over the read-only role); and
* every write (``INSERT`` / ``UPDATE`` / ``DELETE`` / ``TRUNCATE``) — including against a
  table created *after* the grants — is rejected with ``InsufficientPrivilege``.

Gated on ``AI_BOOKS_DB_URL`` so ``./scripts/verify.sh`` stays green without a live
Postgres; runs in CI's ``verify`` job and under ``./scripts/test.sh`` (``-k readonly``).
The static, no-DB guard on the committed SQL lives in ``tests/test_readonly_role.py``.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from ai_books import db
from ai_books.db import migrate
from ai_books.db.repository import AccountRepository
from ai_books.etax import etax_export_snapshot
from ai_books.reports import (
    balance_sheet_snapshot,
    financial_statements_snapshot,
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    worksheet_snapshot,
)
from tests.fixtures import readonly
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

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping read-only viewer role enforcement tests",
)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
#: Throwaway schema + dedicated role, isolated from ``public`` and the other DB tests.
_TEST_SCHEMA = "ai_books_readonly_test"
_RO_ROLE = "ai_books_viewer_ro_test"
#: Local test login password — never a real secret (mirrors the postgres/postgres test cred).
_RO_PASSWORD = "readonly_test"


@dataclass
class _Env:
    """The seeded throwaway schema plus the admin connection that owns it."""

    admin: psycopg.Connection[Any]
    schema: str
    role: str


def _drop_role(admin: psycopg.Connection[Any], role: str) -> None:
    """Drop ``role`` and everything granted to it (idempotent; tolerates absence)."""
    ident = sql.Identifier(role)
    # DROP OWNED clears the CONNECT grant + default-privilege entries that would
    # otherwise block DROP ROLE; it raises "role does not exist" if absent, hence suppress.
    with contextlib.suppress(psycopg.errors.UndefinedObject):
        admin.execute(sql.SQL("DROP OWNED BY {}").format(ident))
    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident))


@pytest.fixture(scope="module")
def readonly_env() -> Iterator[_Env]:
    """Migrate + seed FY2025 into a throwaway schema, then apply the read-only grants.

    Module-scoped: the schema is migrated and seeded once (the read tests never mutate it,
    and the write tests are *rejected* before they can), so every test reuses the same
    fully-built fixture. The role is (re)created from :func:`readonly.grant_statements` —
    the production grant set — then given a login + password so the tests can connect as it.
    """
    admin: psycopg.Connection[Any] = psycopg.connect(
        db.get_db_url(), autocommit=True, row_factory=dict_row
    )
    drop_schema = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(_TEST_SCHEMA))
    try:
        # Clean slate (a previous aborted run may have left the schema/role behind).
        admin.execute(drop_schema)
        _drop_role(admin, _RO_ROLE)

        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(_TEST_SCHEMA)))
        admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(_TEST_SCHEMA)))
        migrate.apply_pending(admin, _MIGRATIONS_DIR)
        load_fiscal_year(admin)

        # Apply the SELECT-only grant set (after tables exist, so GRANT ... ON ALL TABLES
        # covers them), then add a login + password so the tests can connect as the role.
        for statement in readonly.grant_statements(_TEST_SCHEMA, _RO_ROLE):
            admin.execute(statement)
        admin.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(_RO_ROLE), sql.Literal(_RO_PASSWORD)
            )
        )

        yield _Env(admin=admin, schema=_TEST_SCHEMA, role=_RO_ROLE)
    finally:
        admin.execute(drop_schema)
        _drop_role(admin, _RO_ROLE)
        admin.close()


@pytest.fixture
def ro_conn(readonly_env: _Env) -> Iterator[psycopg.Connection[Any]]:
    """A connection authenticated **as the read-only role**, scoped to the test schema."""
    conn: psycopg.Connection[Any] = psycopg.connect(
        db.get_db_url(),
        user=readonly_env.role,
        password=_RO_PASSWORD,
        autocommit=True,
        row_factory=dict_row,
    )
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(readonly_env.schema)))
    try:
        yield conn
    finally:
        conn.close()


# --- reads: every viewer query succeeds as the read-only role ---------------------


def test_chart_of_accounts_is_readable(ro_conn: psycopg.Connection[Any]) -> None:
    """The viewer's first screen (`/` 勘定科目一覧) reads `accounts`."""
    rows = ro_conn.execute("SELECT code FROM accounts ORDER BY code LIMIT 500").fetchall()
    assert rows, "seeded chart of accounts should be visible to the read-only role"
    # The typed repository read path (what the reports build on) also works read-only.
    assert AccountRepository(ro_conn).find()


def test_trial_balance_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    tb = trial_balance_from_db(ro_conn)
    assert tb.is_balanced
    problems = diff_snapshots(load_golden("trial_balance"), trial_balance_snapshot(tb))
    assert problems == [], "trial balance diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


def test_monthly_trend_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    trends = [monthly_trend_from_db(ro_conn, code) for code in MONTHLY_TREND_ACCOUNTS]
    problems = diff_snapshots(load_golden("monthly_trend"), monthly_trend_snapshot(trends))
    assert problems == [], "monthly trend diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


def test_journal_book_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = journal_book_snapshot(journal_book_from_db(ro_conn))
    problems = diff_snapshots(load_golden("journal_book"), snapshot)
    assert problems == [], "journal book diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


def test_general_ledger_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = general_ledger_snapshot(general_ledger_from_db(ro_conn))
    problems = diff_snapshots(load_golden("general_ledger"), snapshot)
    assert problems == [], "general ledger diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


def test_profit_and_loss_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = profit_and_loss_snapshot(profit_and_loss_from_db(ro_conn))
    problems = diff_snapshots(load_golden("profit_and_loss"), snapshot)
    assert problems == [], "P&L diverged over read-only role:\n  - " + "\n  - ".join(problems)


def test_balance_sheet_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = balance_sheet_snapshot(balance_sheet_from_db(ro_conn))
    problems = diff_snapshots(load_golden("balance_sheet"), snapshot)
    assert problems == [], "balance sheet diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


def test_worksheet_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = worksheet_snapshot(worksheet_from_db(ro_conn))
    problems = diff_snapshots(load_golden("worksheet"), snapshot)
    assert problems == [], "worksheet diverged over read-only role:\n  - " + "\n  - ".join(problems)


def test_financial_statements_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = financial_statements_snapshot(financial_statements_from_db(ro_conn))
    problems = diff_snapshots(load_golden("financial_statements"), snapshot)
    assert problems == [], "決算書 diverged over read-only role:\n  - " + "\n  - ".join(problems)


def test_etax_export_matches_golden_read_only(ro_conn: psycopg.Connection[Any]) -> None:
    snapshot = etax_export_snapshot(etax_export_from_db(ro_conn))
    problems = diff_snapshots(load_golden("etax_export"), snapshot)
    assert problems == [], "e-Tax export diverged over read-only role:\n  - " + "\n  - ".join(
        problems
    )


# --- writes: every mutation is rejected -------------------------------------------


@pytest.mark.parametrize(
    ("label", "statement"),
    [
        (
            "INSERT",
            "INSERT INTO accounts (code, name, account_type, normal_balance) "
            "VALUES ('9999', 'should fail', 'asset', 'debit')",
        ),
        ("UPDATE", "UPDATE accounts SET name = 'tampered' WHERE code = '1110'"),
        ("DELETE", "DELETE FROM journal_lines"),
        ("TRUNCATE", "TRUNCATE journal_lines"),
    ],
)
def test_write_is_rejected(ro_conn: psycopg.Connection[Any], label: str, statement: str) -> None:
    """No mutation reaches the data: Postgres denies it for lack of privilege."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ro_conn.execute(statement)


def test_seeded_data_is_unchanged_after_rejected_writes(
    readonly_env: _Env, ro_conn: psycopg.Connection[Any]
) -> None:
    """The rejected writes above truly changed nothing (privilege check, not a silent no-op)."""
    # Compare row counts the admin sees vs. what the read-only role still reads.
    for table in ("accounts", "journal_entries", "journal_lines"):
        admin_count = readonly_env.admin.execute(
            sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                sql.Identifier(readonly_env.schema), sql.Identifier(table)
            )
        ).fetchone()
        ro_count = ro_conn.execute(
            sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
        ).fetchone()
        assert admin_count is not None
        assert ro_count is not None
        assert admin_count["n"] == ro_count["n"]
        assert ro_count["n"] > 0  # the seed is intact


def test_future_table_is_readable_but_not_writable(
    readonly_env: _Env, ro_conn: psycopg.Connection[Any]
) -> None:
    """ALTER DEFAULT PRIVILEGES keeps a *future* table read-only (readable, not writable).

    A table created after the grants ran must inherit SELECT (so a later migration's table
    shows up in the viewer) yet still reject writes — proving the grant covers tomorrow's
    schema, not just today's.
    """
    probe = sql.Identifier(readonly_env.schema, "ro_future_probe")
    admin = readonly_env.admin
    admin.execute(sql.SQL("CREATE TABLE {} (id int)").format(probe))
    try:
        admin.execute(sql.SQL("INSERT INTO {} (id) VALUES (1)").format(probe))

        # Read: visible to the read-only role via the default-privilege grant.
        row = ro_conn.execute("SELECT id FROM ro_future_probe").fetchone()
        assert row is not None
        assert row["id"] == 1

        # Write: still rejected.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro_conn.execute("INSERT INTO ro_future_probe (id) VALUES (2)")
    finally:
        admin.execute(sql.SQL("DROP TABLE {}").format(probe))

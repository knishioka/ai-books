"""DB-backed property invariants — the 取消 property + 往復 over a real Postgres (Issue #57).

Two invariants can only be asserted against the database, because they are about the *storage* path,
not the pure reducers:

* **取消の性質** — a ``voided`` 仕訳 leaves no trace in 残高/試算表/PL/BS. The exclusion lives in the
  repository's SQL ``status`` filter, so the property is: load a balanced year, mark an arbitrary subset
  取消, and the 記帳確定 reports read from the DB must equal the offline reduction over the *remaining*
  entries. The 全額 voided case (every entry cancelled ⇒ empty books) falls out as a boundary.
* **Decimal 往復誤差ゼロ** — an arbitrary balanced year written to ``numeric(18, 2)`` and read back
  through the production engine reproduces the offline 試算表 byte-for-byte (no rounding in the round
  trip).

Each Hypothesis example needs its *own* clean database, so the test creates a throwaway schema inside
the example (not via a function-scoped fixture — which Hypothesis does not reset between examples) and
drops it after. Examples are capped low and deadlines disabled because every example pays a migrate +
seed. The module skips entirely when ``AI_BOOKS_DB_URL`` is unset, so ``./scripts/verify.sh`` stays
green without a live Postgres; ``./scripts/test.sh`` runs it against the container.

The tail re-checks the エッジケース years through the production SQL engine (dual-path), so the committed
edge golden is proven correct against *both* the offline reducer and real Postgres, not just one.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from hypothesis import given, settings
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from ai_books import db
from ai_books.db import migrate
from ai_books.db.repository import JournalRepository
from ai_books.reports import balance_sheet_snapshot, profit_and_loss_snapshot
from tests.fixtures.seed_fy import (
    EDGE_DATASETS,
    SeedEntry,
    balance_sheet_from_dataset,
    balance_sheet_from_db,
    diff_snapshots,
    load_fiscal_year,
    profit_and_loss_from_dataset,
    profit_and_loss_from_db,
    trial_balance_from_db,
    trial_balance_snapshot,
)
from tests.fixtures.seed_fy.generators import balanced_datasets, datasets_with_voided
from tests.fixtures.seed_fy.reports import trial_balance_from_dataset

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed property tests",
)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

#: Per-example schema names must not collide across examples in one run; a monotonic counter is enough
#: (uniqueness, not randomness, is what matters — and ``Math.random`` is irrelevant here).
_schema_counter = itertools.count()


@contextmanager
def _migrated_schema() -> Iterator[psycopg.Connection[DictRow]]:
    """Yield a dict-row connection on a fresh, fully-migrated throwaway schema, dropped on exit.

    Mirrors the ``migrated_conn`` fixture, but as a context manager so each Hypothesis example gets its
    own clean database (a function-scoped fixture is set up once per test, not once per example).
    """
    schema = f"ai_books_prop_{next(_schema_counter)}"
    connection: psycopg.Connection[Any] = psycopg.connect(
        db.get_db_url(), autocommit=True, row_factory=dict_row
    )
    drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
    try:
        connection.execute(drop)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migrate.apply_pending(connection, _MIGRATIONS_DIR)
        yield connection
    finally:
        connection.execute(drop)
        connection.close()


def _void(conn: psycopg.Connection[DictRow], vouchers: frozenset[str]) -> None:
    """Mark every entry whose ``voucher_no`` is in ``vouchers`` 取消, via the production write path."""
    repo = JournalRepository(conn)
    for voucher in vouchers:
        row = conn.execute(
            "SELECT id FROM journal_entries WHERE voucher_no = %s", (voucher,)
        ).fetchone()
        assert row is not None, f"entry {voucher} not loaded"
        repo.mark_voided(int(row["id"]), "property test 取消")


def _posted_snapshots(conn: psycopg.Connection[DictRow]) -> dict[str, Any]:
    """The 記帳確定 (POSTED) 試算表 / PL / BS read from ``conn``, as comparable golden-shaped dicts."""
    return {
        "trial_balance": trial_balance_snapshot(trial_balance_from_db(conn)),
        "profit_and_loss": profit_and_loss_snapshot(profit_and_loss_from_db(conn)),
        "balance_sheet": balance_sheet_snapshot(balance_sheet_from_db(conn)),
    }


def _offline_snapshots(entries: tuple[SeedEntry, ...]) -> dict[str, Any]:
    """The same three reports reduced offline from ``entries`` — the independent source of truth."""
    return {
        "trial_balance": trial_balance_snapshot(trial_balance_from_dataset(entries)),
        "profit_and_loss": profit_and_loss_snapshot(profit_and_loss_from_dataset(entries)),
        "balance_sheet": balance_sheet_snapshot(balance_sheet_from_dataset(entries)),
    }


def _assert_reports_agree(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    for report in expected:
        problems = diff_snapshots(expected[report], actual[report])
        assert problems == [], f"{report} diverged:\n  - " + "\n  - ".join(problems)


# ── Decimal 往復 (numeric(18, 2) round-trip) ─────────────────────────────────────


@settings(max_examples=15, deadline=None)
@given(balanced_datasets(min_size=1, max_size=6))
def test_decimal_round_trips_through_postgres(entries: tuple[SeedEntry, ...]) -> None:
    # AC: 金額 Decimal は往復で誤差ゼロ — an arbitrary balanced year written to numeric(18, 2) and read
    # back through the production engine reproduces the offline 試算表 exactly.
    with _migrated_schema() as conn:
        load_fiscal_year(conn, entries)
        from_db = trial_balance_snapshot(trial_balance_from_db(conn))
    from_dataset = trial_balance_snapshot(trial_balance_from_dataset(entries))
    problems = diff_snapshots(from_dataset, from_db)
    assert problems == [], "DB round-trip lost precision:\n  - " + "\n  - ".join(problems)


# ── 取消の性質 (voided entries leave no trace) ────────────────────────────────────


@settings(max_examples=15, deadline=None)
@given(datasets_with_voided())
def test_voided_entries_are_excluded_from_every_report(
    case: tuple[tuple[SeedEntry, ...], frozenset[str]],
) -> None:
    # Invariant (取消の性質): after voiding an arbitrary subset, the 記帳確定 残高/試算表/PL/BS read from
    # the DB equal the offline reduction over only the *remaining* entries — the 取消 vanish completely.
    entries, voided = case
    remaining = tuple(entry for entry in entries if entry.voucher_no not in voided)
    with _migrated_schema() as conn:
        load_fiscal_year(conn, entries)
        _void(conn, voided)
        actual = _posted_snapshots(conn)
    _assert_reports_agree(_offline_snapshots(remaining), actual)


@settings(max_examples=10, deadline=None)
@given(balanced_datasets(min_size=1, max_size=5))
def test_all_voided_year_reports_as_empty(entries: tuple[SeedEntry, ...]) -> None:
    # Boundary (全額 voided): cancel *every* entry ⇒ the books read empty — no 試算表 rows, zero footings,
    # zero 当期純利益, and a (trivially) balanced 貸借対照表.
    all_vouchers = frozenset(entry.voucher_no for entry in entries)
    with _migrated_schema() as conn:
        load_fiscal_year(conn, entries)
        _void(conn, all_vouchers)
        trial_balance = trial_balance_from_db(conn)
        profit_and_loss = profit_and_loss_from_db(conn)
        balance_sheet = balance_sheet_from_db(conn)
    assert trial_balance.rows == []
    assert trial_balance.total_debit == trial_balance.total_credit == Decimal(0)
    assert profit_and_loss.net_income == Decimal(0)
    assert balance_sheet.is_balanced
    assert balance_sheet.total_assets == Decimal(0)


# ── エッジケース years through the production SQL engine (dual-path) ─────────────────


@pytest.mark.parametrize("name", sorted(EDGE_DATASETS))
def test_edge_dataset_matches_offline_reduction_over_db(
    name: str, migrated_conn: psycopg.Connection[DictRow]
) -> None:
    # AC: 追加エッジケースで集計/PL/BS が正しい — each edge year, read back through the production engine,
    # equals its offline reduction (the golden source). Pins the edge golden against real Postgres too.
    entries = EDGE_DATASETS[name]
    load_fiscal_year(migrated_conn, entries)
    _assert_reports_agree(_offline_snapshots(entries), _posted_snapshots(migrated_conn))

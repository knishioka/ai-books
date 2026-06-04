"""DB-backed tests: seeding the chart of accounts and the read MCP tool helpers.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green
without a live Postgres); runs in CI on the throwaway-schema ``migrated_conn``
fixture. Covers the acceptance criteria that need a real round-trip: idempotent
re-seed, full 表示区分 coverage in the DB, and the typed list/get/search results.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.errors import RecordNotFoundError
from ai_books.models import Account, AccountType, StatementCategory
from ai_books.seed.accounts import CHART_OF_ACCOUNTS, REQUIRED_CATEGORIES, seed_accounts
from ai_books.server import _get_account, _list_accounts, _search_accounts

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed seed tests",
)


def _count(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM accounts")
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def test_seed_inserts_full_chart(migrated_conn: psycopg.Connection[Any]) -> None:
    result = seed_accounts(migrated_conn)
    assert result.total == len(CHART_OF_ACCOUNTS)
    assert result.inserted == len(CHART_OF_ACCOUNTS)
    assert _count(migrated_conn) == len(CHART_OF_ACCOUNTS)


def test_seed_is_idempotent(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: re-seeding does not duplicate rows.
    first = seed_accounts(migrated_conn)
    after_first = _count(migrated_conn)

    second = seed_accounts(migrated_conn)
    assert second.inserted == 0
    assert _count(migrated_conn) == after_first == first.inserted


def test_seed_populates_every_statement_category(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: each 青色申告決算書 表示区分 has ≥1 account after seeding.
    seed_accounts(migrated_conn)
    with migrated_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT statement_category FROM accounts")
        present = {StatementCategory(row[0]) for row in cur.fetchall() if row[0] is not None}
    assert present >= REQUIRED_CATEGORIES


def test_seed_resolves_parent_child(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)
    futsu = _get_account(migrated_conn, "1141")  # 普通預金
    yokin = _get_account(migrated_conn, "1140")  # 預金 (parent)
    assert futsu.parent_id == yokin.id


def test_list_accounts_returns_typed_models(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: list_accounts returns typed (Pydantic) results.
    seed_accounts(migrated_conn)
    everything = _list_accounts(migrated_conn)
    assert len(everything) == len(CHART_OF_ACCOUNTS)
    assert all(isinstance(a, Account) for a in everything)
    # Ordered by code.
    assert everything == sorted(everything, key=lambda a: a.code)


def test_list_accounts_filters_by_type_and_category(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    seed_accounts(migrated_conn)

    assets = _list_accounts(migrated_conn, account_type=AccountType.ASSET)
    assert assets
    assert all(a.account_type is AccountType.ASSET for a in assets)

    cogs = _list_accounts(migrated_conn, statement_category=StatementCategory.COST_OF_GOODS_SOLD)
    assert {a.code for a in cogs} == {"5110", "5120", "5130"}


def test_list_accounts_filters_by_is_active(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)
    migrated_conn.execute("UPDATE accounts SET is_active = false WHERE code = %s", ("1110",))

    active = _list_accounts(migrated_conn, is_active=True)
    assert all(a.is_active for a in active)
    assert "1110" not in {a.code for a in active}

    inactive = _list_accounts(migrated_conn, is_active=False)
    assert {a.code for a in inactive} == {"1110"}


def test_get_account_by_code(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: get_account returns a typed result.
    seed_accounts(migrated_conn)
    cash = _get_account(migrated_conn, "1110")
    assert isinstance(cash, Account)
    assert cash.name == "現金"
    assert cash.account_type is AccountType.ASSET


def test_get_account_missing_raises(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)
    with pytest.raises(RecordNotFoundError):
        _get_account(migrated_conn, "0000")


def test_search_accounts_matches_name_and_code(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)

    by_name = _search_accounts(migrated_conn, "預金")
    assert {"1140", "1141", "1142"} <= {a.code for a in by_name}

    by_code = _search_accounts(migrated_conn, "711")
    assert {a.code for a in by_code} == {"7110"}


def test_search_excludes_inactive_by_default(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)
    migrated_conn.execute("UPDATE accounts SET is_active = false WHERE code = %s", ("1110",))

    assert _search_accounts(migrated_conn, "現金") == []
    included = _search_accounts(migrated_conn, "現金", include_inactive=True)
    assert {a.code for a in included} == {"1110"}

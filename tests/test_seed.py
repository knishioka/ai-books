"""Non-DB tests for the chart-of-accounts seed data and its integrity validation.

These run everywhere (no ``AI_BOOKS_DB_URL`` needed): they exercise the seed
*definition* and :func:`validate_chart`. The DB round-trip (idempotency, the read
tools) lives in ``test_seed_db.py``.
"""

from __future__ import annotations

import pytest

from ai_books.errors import SeedIntegrityError
from ai_books.models import (
    STATEMENT_CATEGORY_ACCOUNT_TYPE,
    Account,
    AccountType,
    NormalSide,
    StatementCategory,
)
from ai_books.seed.accounts import (
    CHART_OF_ACCOUNTS,
    REQUIRED_CATEGORIES,
    SeedAccount,
    validate_chart,
)


def test_canonical_chart_is_consistent() -> None:
    # The shipped chart passes its own integrity gate.
    validate_chart()


def test_every_statement_category_has_an_account() -> None:
    # AC: after seed, every 青色申告決算書 表示区分 has at least one account.
    covered = {row.statement_category for row in CHART_OF_ACCOUNTS}
    assert covered >= REQUIRED_CATEGORIES
    assert set(StatementCategory) == REQUIRED_CATEGORIES


def test_codes_are_unique() -> None:
    codes = [row.code for row in CHART_OF_ACCOUNTS]
    assert len(codes) == len(set(codes))


def test_parent_codes_resolve_and_precede_children() -> None:
    seen: set[str] = set()
    for row in CHART_OF_ACCOUNTS:
        if row.parent_code is not None:
            assert row.parent_code in seen, f"{row.code} parent {row.parent_code} not yet defined"
        seen.add(row.code)


def test_every_seed_row_builds_a_valid_account() -> None:
    # The seed data must round-trip through the frozen Pydantic contract.
    for row in CHART_OF_ACCOUNTS:
        account = Account(
            code=row.code,
            name=row.name,
            account_type=row.account_type,
            statement_category=row.statement_category,
            normal_balance=row.normal_balance,
        )
        assert account.account_type is STATEMENT_CATEGORY_ACCOUNT_TYPE[row.statement_category]


def test_validate_chart_detects_normal_balance_mismatch() -> None:
    # AC: a seed whose 区分 and 正常残高 disagree is detected.
    bad = SeedAccount(
        code="9999",
        name="壊れた資産",
        account_type=AccountType.ASSET,  # 資産 → 借方が正常
        normal_balance=NormalSide.CREDIT,  # ...but flagged credit
        statement_category=StatementCategory.CURRENT_ASSETS,
    )
    with pytest.raises(SeedIntegrityError) as excinfo:
        validate_chart((bad,))
    assert any("normal" in problem for problem in excinfo.value.problems)


def test_validate_chart_detects_category_type_mismatch() -> None:
    # A 売上 bucket on an expense account is rejected.
    bad = SeedAccount(
        code="9998",
        name="区分違い",
        account_type=AccountType.EXPENSE,
        normal_balance=NormalSide.DEBIT,
        statement_category=StatementCategory.SALES,  # SALES requires revenue
    )
    with pytest.raises(SeedIntegrityError) as excinfo:
        validate_chart((bad,))
    assert any("表示区分" in problem for problem in excinfo.value.problems)


def test_validate_chart_detects_missing_category_coverage() -> None:
    # A chart that only maps one category leaves the rest uncovered.
    only_sales = (
        SeedAccount(
            code="4110",
            name="売上高",
            account_type=AccountType.REVENUE,
            normal_balance=NormalSide.CREDIT,
            statement_category=StatementCategory.SALES,
        ),
    )
    with pytest.raises(SeedIntegrityError) as excinfo:
        validate_chart(only_sales)
    assert any("no account assigned" in problem for problem in excinfo.value.problems)


def test_validate_chart_detects_duplicate_codes() -> None:
    dup = SeedAccount(
        code="1110",
        name="現金",
        account_type=AccountType.ASSET,
        normal_balance=NormalSide.DEBIT,
        statement_category=StatementCategory.CURRENT_ASSETS,
    )
    with pytest.raises(SeedIntegrityError) as excinfo:
        validate_chart((dup, dup))
    assert any("duplicate code" in problem for problem in excinfo.value.problems)


def test_validate_chart_detects_dangling_parent() -> None:
    orphan = SeedAccount(
        code="1141",
        name="普通預金",
        account_type=AccountType.ASSET,
        normal_balance=NormalSide.DEBIT,
        statement_category=StatementCategory.CURRENT_ASSETS,
        parent_code="1140",  # never defined
    )
    with pytest.raises(SeedIntegrityError) as excinfo:
        validate_chart((orphan,))
    assert any("parent" in problem for problem in excinfo.value.problems)

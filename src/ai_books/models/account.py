"""勘定科目 — chart-of-accounts domain model.

Mirrors the ``accounts`` table (see ``supabase/migrations/..._accounts.sql``) and
re-enforces, at the Pydantic layer, the two constraints the table guards in SQL:
normal balance must agree with the account type, and an account cannot be its own
parent. Enforcing them here too means the MCP entry point rejects bad input with a
machine-readable error *before* a row is ever attempted (invariant #2).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from .base import DomainModel
from .enums import (
    STATEMENT_CATEGORY_ACCOUNT_TYPE,
    AccountType,
    NormalSide,
    StatementCategory,
    normal_side_for,
)


class Account(DomainModel):
    """A single account in the chart of accounts (勘定科目)."""

    id: int | None = None
    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    account_type: AccountType  # 科目区分
    statement_category: StatementCategory | None = None  # 集計分類 (決算書の表示区分)
    normal_balance: NormalSide  # 正常残高
    parent_id: int | None = None  # 内訳 (上位科目)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_normal_balance_matches_type(self) -> Account:
        expected = normal_side_for(self.account_type)
        if self.normal_balance != expected:
            raise ValueError(
                f"normal_balance for {self.account_type.value} accounts must be "
                f"{expected.value}, got {self.normal_balance.value}"
            )
        return self

    @model_validator(mode="after")
    def _check_not_self_parent(self) -> Account:
        if self.parent_id is not None and self.id is not None and self.parent_id == self.id:
            raise ValueError("account cannot be its own parent")
        return self

    @model_validator(mode="after")
    def _check_statement_category_matches_type(self) -> Account:
        if self.statement_category is None:
            return self
        expected = STATEMENT_CATEGORY_ACCOUNT_TYPE[self.statement_category]
        if self.account_type != expected:
            raise ValueError(
                f"statement_category {self.statement_category.value} requires "
                f"account_type {expected.value}, got {self.account_type.value}"
            )
        return self

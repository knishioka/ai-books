"""Enum types mirroring the Postgres ``ENUM`` types defined in the migrations.

Each enum's ``value`` is exactly the string stored in Postgres, so a model
round-trips through the database without translation. ``normal_side`` and
``entry_side`` are distinct Postgres types that happen to share members
(``debit`` / ``credit``); we keep them as separate Python enums to preserve that
distinction in the type contract.
"""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    """科目区分 — account classification (Postgres ``account_type``)."""

    ASSET = "asset"  # 資産
    LIABILITY = "liability"  # 負債
    EQUITY = "equity"  # 純資産
    REVENUE = "revenue"  # 収益
    EXPENSE = "expense"  # 費用


class StatementCategory(StrEnum):
    """決算書の表示区分 — where an account rolls up on the 青色申告決算書.

    These are the aggregation buckets that make P/L and B/S generation mechanical
    (#18/#20/#21/#23 map each account onto one of these). Stored verbatim in the
    ``accounts.statement_category`` text column.
    """

    # 損益計算書 (P/L)
    SALES = "sales"  # 売上(収入)金額
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"  # 売上原価
    SELLING_ADMIN_EXPENSES = "selling_admin_expenses"  # 経費 (販管費)
    NON_OPERATING_INCOME = "non_operating_income"  # 営業外収益
    NON_OPERATING_EXPENSES = "non_operating_expenses"  # 営業外費用
    # 製造原価の計算 (#23)
    MANUFACTURING_MATERIALS = "manufacturing_materials"  # 材料費
    MANUFACTURING_LABOR = "manufacturing_labor"  # 労務費
    MANUFACTURING_OVERHEAD = "manufacturing_overhead"  # 製造経費
    # 貸借対照表 (B/S)
    CURRENT_ASSETS = "current_assets"  # 流動資産
    FIXED_ASSETS = "fixed_assets"  # 固定資産
    CURRENT_LIABILITIES = "current_liabilities"  # 流動負債
    FIXED_LIABILITIES = "fixed_liabilities"  # 固定負債
    EQUITY = "equity"  # 純資産 (元入金・事業主借 等)


class NormalSide(StrEnum):
    """正常残高 — an account's normal balance side (Postgres ``normal_side``)."""

    DEBIT = "debit"  # 借方
    CREDIT = "credit"  # 貸方


class EntrySide(StrEnum):
    """明細の計上方向 — the side a journal line is recorded on (``entry_side``)."""

    DEBIT = "debit"  # 借方
    CREDIT = "credit"  # 貸方


class EntryStatus(StrEnum):
    """伝票の状態 — journal entry lifecycle (Postgres ``entry_status``)."""

    DRAFT = "draft"  # 起票途中
    POSTED = "posted"  # 記帳確定


#: Account types whose normal balance is the debit side (資産 / 費用).
DEBIT_NORMAL_TYPES = frozenset({AccountType.ASSET, AccountType.EXPENSE})

#: Account types whose normal balance is the credit side (負債 / 純資産 / 収益).
CREDIT_NORMAL_TYPES = frozenset({AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE})


def normal_side_for(account_type: AccountType) -> NormalSide:
    """Return the normal-balance side implied by ``account_type``.

    Mirrors the ``accounts_normal_balance_matches_type`` CHECK constraint:
    資産/費用 → 借方, 負債/純資産/収益 → 貸方.
    """
    return NormalSide.DEBIT if account_type in DEBIT_NORMAL_TYPES else NormalSide.CREDIT


#: The account type each 表示区分 must belong to. A 売上 bucket can only hold
#: revenue accounts, a 流動資産 bucket only assets, and so on — this ties the
#: statement layout back to double-entry classification so report generation
#: (#18/#20/#21/#23) can trust the grouping.
STATEMENT_CATEGORY_ACCOUNT_TYPE: dict[StatementCategory, AccountType] = {
    StatementCategory.SALES: AccountType.REVENUE,
    StatementCategory.NON_OPERATING_INCOME: AccountType.REVENUE,
    StatementCategory.COST_OF_GOODS_SOLD: AccountType.EXPENSE,
    StatementCategory.SELLING_ADMIN_EXPENSES: AccountType.EXPENSE,
    StatementCategory.NON_OPERATING_EXPENSES: AccountType.EXPENSE,
    StatementCategory.MANUFACTURING_MATERIALS: AccountType.EXPENSE,
    StatementCategory.MANUFACTURING_LABOR: AccountType.EXPENSE,
    StatementCategory.MANUFACTURING_OVERHEAD: AccountType.EXPENSE,
    StatementCategory.CURRENT_ASSETS: AccountType.ASSET,
    StatementCategory.FIXED_ASSETS: AccountType.ASSET,
    StatementCategory.CURRENT_LIABILITIES: AccountType.LIABILITY,
    StatementCategory.FIXED_LIABILITIES: AccountType.LIABILITY,
    StatementCategory.EQUITY: AccountType.EQUITY,
}

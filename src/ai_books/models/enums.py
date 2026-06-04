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

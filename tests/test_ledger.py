"""Unit tests for the pure ledger arithmetic — no database required.

Covers the rules :mod:`ai_books.ledger` owns: which side moves a balance, how a
debit/credit pair is signed into the normal direction, and how a running balance
accumulates down a sequence of lines.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ai_books.ledger import (
    RawLedgerLine,
    balance_from_totals,
    build_ledger_rows,
    signed_delta,
)
from ai_books.models import EntrySide, NormalSide


@pytest.mark.parametrize(
    ("side", "normal", "expected"),
    [
        (EntrySide.DEBIT, NormalSide.DEBIT, Decimal("100")),  # 資産/費用を借方で増やす
        (EntrySide.CREDIT, NormalSide.DEBIT, Decimal("-100")),  # 借方正常を貸方で減らす
        (EntrySide.CREDIT, NormalSide.CREDIT, Decimal("100")),  # 負債/収益を貸方で増やす
        (EntrySide.DEBIT, NormalSide.CREDIT, Decimal("-100")),  # 貸方正常を借方で減らす
    ],
)
def test_signed_delta(side: EntrySide, normal: NormalSide, expected: Decimal) -> None:
    assert signed_delta(side, normal, Decimal("100")) == expected


def test_balance_from_totals_debit_normal() -> None:
    # 資産/費用: 借方 - 貸方
    assert balance_from_totals(Decimal("1500"), Decimal("300"), NormalSide.DEBIT) == Decimal("1200")


def test_balance_from_totals_credit_normal() -> None:
    # 負債/純資産/収益: 貸方 - 借方
    assert balance_from_totals(Decimal("300"), Decimal("1500"), NormalSide.CREDIT) == Decimal(
        "1200"
    )


def test_balance_from_totals_goes_negative_when_opposite_normal() -> None:
    # A debit-normal account with more credits than debits sits below zero.
    assert balance_from_totals(Decimal("100"), Decimal("400"), NormalSide.DEBIT) == Decimal("-300")


def _raw(line_no: int, side: EntrySide, amount: str) -> RawLedgerLine:
    return RawLedgerLine(
        entry_id=line_no,
        line_no=line_no,
        entry_date=date(2026, 4, line_no),
        voucher_no=f"V{line_no:07d}",
        description=None,
        line_description=None,
        counter_accounts=[],
        side=side,
        amount=Decimal(amount),
    )


def test_build_ledger_rows_running_balance_debit_normal() -> None:
    lines = [
        _raw(1, EntrySide.DEBIT, "1000"),
        _raw(2, EntrySide.DEBIT, "500"),
        _raw(3, EntrySide.CREDIT, "300"),
    ]
    rows, closing = build_ledger_rows(lines, NormalSide.DEBIT, Decimal("0"))

    assert [r.running_balance for r in rows] == [Decimal("1000"), Decimal("1500"), Decimal("1200")]
    assert closing == Decimal("1200")
    # Inputs are echoed back onto the rows unchanged.
    assert [r.line_no for r in rows] == [1, 2, 3]
    assert rows[2].side is EntrySide.CREDIT


def test_build_ledger_rows_starts_from_opening_balance() -> None:
    lines = [_raw(1, EntrySide.CREDIT, "200")]
    rows, closing = build_ledger_rows(lines, NormalSide.CREDIT, Decimal("1000"))

    # Credit-normal: a credit line *increases* the balance.
    assert rows[0].running_balance == Decimal("1200")
    assert closing == Decimal("1200")


def test_build_ledger_rows_empty_returns_opening() -> None:
    rows, closing = build_ledger_rows([], NormalSide.DEBIT, Decimal("777"))
    assert rows == []
    assert closing == Decimal("777")

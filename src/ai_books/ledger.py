"""Pure ledger arithmetic — the accounting math behind the read tools.

Kept deliberately free of SQL and I/O so the *rules* (which side increases an
account, how a balance is signed, how a running balance accumulates) are unit
tested without a database. :mod:`ai_books.db.repository` supplies the rows; this
module turns them into signed balances.

The single rule everything derives from: a line *increases* an account's balance
when its side matches the account's normal side, and *decreases* it otherwise.
資産/費用 (借方が正常) grow on the debit side; 負債/純資産/収益 (貸方が正常) grow on
the credit side.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TypedDict

from ai_books.models.enums import EntrySide, NormalSide
from ai_books.models.query import LedgerRow


class RawLedgerLine(TypedDict):
    """A journal line touching the ledger account, before the running balance is known."""

    entry_id: int
    line_no: int
    entry_date: date
    voucher_no: str | None
    description: str | None
    line_description: str | None
    counter_accounts: list[str]
    side: EntrySide
    amount: Decimal


def signed_delta(side: EntrySide, normal: NormalSide, amount: Decimal) -> Decimal:
    """Return ``amount`` signed by its effect on a ``normal``-balance account.

    Positive when ``side`` matches the account's normal side (the line increases
    the balance), negative when it is the opposite side. ``EntrySide`` and
    ``NormalSide`` are distinct enums that share their string values, so they are
    compared by value.
    """
    increases = side.value == normal.value
    return amount if increases else -amount


def balance_from_totals(debit_total: Decimal, credit_total: Decimal, normal: NormalSide) -> Decimal:
    """Sign a pair of debit/credit totals into the account's normal direction.

    Debit-normal (資産/費用) → ``debit_total - credit_total``; credit-normal
    (負債/純資産/収益) → ``credit_total - debit_total``. A negative result means the
    account sits opposite its normal balance.
    """
    if normal is NormalSide.DEBIT:
        return debit_total - credit_total
    return credit_total - debit_total


def build_ledger_rows(
    raw_lines: list[RawLedgerLine],
    normal: NormalSide,
    opening_balance: Decimal,
) -> tuple[list[LedgerRow], Decimal]:
    """Attach a running balance to each line; return the rows and the closing balance.

    ``raw_lines`` must already be ordered the way the ledger should read
    (chronologically: entry_date, then entry id, then line_no). The running balance
    starts from ``opening_balance`` (the 繰越 carried in from before the window) and
    each line moves it by :func:`signed_delta`. With no lines the closing balance is
    just the opening balance.
    """
    running = opening_balance
    rows: list[LedgerRow] = []
    for line in raw_lines:
        running += signed_delta(line["side"], normal, line["amount"])
        rows.append(
            LedgerRow(
                entry_id=line["entry_id"],
                line_no=line["line_no"],
                entry_date=line["entry_date"],
                voucher_no=line["voucher_no"],
                description=line["description"],
                line_description=line["line_description"],
                counter_accounts=line["counter_accounts"],
                side=line["side"],
                amount=line["amount"],
                running_balance=running,
            )
        )
    return rows, running

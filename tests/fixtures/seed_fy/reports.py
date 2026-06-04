"""Trial-balance report over the synthetic year — pure and DB-backed.

The golden harness compares two independent computations of the *same* trial balance:

* :func:`trial_balance_from_dataset` reduces :data:`~tests.fixtures.seed_fy.dataset.FY_ENTRIES`
  in pure Python (no database). This is what generates the committed golden file.
* :func:`trial_balance_from_db` aggregates ``journal_lines`` with SQL after the dataset
  has been loaded into Postgres. This is what the pytest harness checks against golden.

Having two code paths that must agree turns the golden test into a genuine cross-check
of the storage/aggregation round-trip (Decimal preserved, no line dropped, sign correct),
not just a tautology. Both share the signing rule in :func:`ai_books.ledger.balance_from_totals`.

A trial balance is the foundation every later report (#18 集計 / #20 PL / #21 BS / #23 決算書)
builds on, so this is the first — and most reusable — golden snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple

from ai_books import ledger
from ai_books.models import EntrySide, EntryStatus, NormalSide

from .dataset import FY_ENTRIES, SeedEntry, account_name, normal_side

if TYPE_CHECKING:
    import psycopg


class TrialBalanceRow(NamedTuple):
    """One account's footing in the trial balance (試算表の一行).

    ``balance`` is signed into the account's 正常残高 direction (so a contra account
    such as 期末商品棚卸高 shows a negative balance), exactly like
    :class:`ai_books.models.AccountBalance`.
    """

    code: str
    name: str
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal


class TrialBalance(NamedTuple):
    """A full trial balance: per-account rows plus the two column footings.

    ``total_debit`` / ``total_credit`` are the sums of the per-account ``debit_total`` /
    ``credit_total`` columns; they are equal iff the books balance overall (借貸平均).
    """

    rows: tuple[TrialBalanceRow, ...]
    total_debit: Decimal
    total_credit: Decimal

    @property
    def is_balanced(self) -> bool:
        """True when the debit and credit column footings are equal."""
        return self.total_debit == self.total_credit


def _assemble(
    debit_by_code: dict[str, Decimal],
    credit_by_code: dict[str, Decimal],
    name_of: dict[str, str],
    normal_of: dict[str, NormalSide],
) -> TrialBalance:
    """Build a :class:`TrialBalance` from per-code debit/credit sums, ordered by code."""
    rows: list[TrialBalanceRow] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for code in sorted(debit_by_code.keys() | credit_by_code.keys()):
        debit = debit_by_code.get(code, Decimal(0))
        credit = credit_by_code.get(code, Decimal(0))
        balance = ledger.balance_from_totals(debit, credit, normal_of[code])
        rows.append(TrialBalanceRow(code, name_of[code], debit, credit, balance))
        total_debit += debit
        total_credit += credit
    return TrialBalance(tuple(rows), total_debit, total_credit)


def trial_balance_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> TrialBalance:
    """Reduce the in-memory dataset into a trial balance — no database required."""
    debit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        for line in entry.lines:
            bucket = debit_by_code if line.side is EntrySide.DEBIT else credit_by_code
            bucket[line.account_code] += line.amount

    codes = debit_by_code.keys() | credit_by_code.keys()
    name_of = {code: account_name(code) for code in codes}
    normal_of = {code: normal_side(code) for code in codes}
    return _assemble(dict(debit_by_code), dict(credit_by_code), name_of, normal_of)


def trial_balance_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> TrialBalance:
    """Aggregate ``journal_lines`` into a trial balance with one SQL GROUP BY.

    Independent of :func:`trial_balance_from_dataset` (raw SQL, not the in-memory
    reduction) so a divergence between the two surfaces a storage or aggregation bug.
    ``status`` filters which entries are summed (default: 記帳確定 only).
    """
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT a.code, a.name, a.normal_balance,
                   COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)  AS debit_total,
                   COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            JOIN accounts a ON a.id = jl.account_id
            WHERE (%(status)s::entry_status IS NULL OR je.status = %(status)s::entry_status)
            GROUP BY a.code, a.name, a.normal_balance
            ORDER BY a.code
            """,
            {"status": status.value if status is not None else None},
        )
        db_rows = cur.fetchall()

    debit_by_code: dict[str, Decimal] = {}
    credit_by_code: dict[str, Decimal] = {}
    name_of: dict[str, str] = {}
    normal_of: dict[str, NormalSide] = {}
    for row in db_rows:
        code = row["code"]
        debit_by_code[code] = Decimal(row["debit_total"])
        credit_by_code[code] = Decimal(row["credit_total"])
        name_of[code] = row["name"]
        normal_of[code] = NormalSide(row["normal_balance"])
    return _assemble(debit_by_code, credit_by_code, name_of, normal_of)

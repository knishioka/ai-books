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
from ai_books.db.repository import JournalRepository, LedgerRepository
from ai_books.models import (
    EntrySide,
    EntryStatus,
    GeneralLedger,
    GeneralLedgerAccount,
    GeneralLedgerRow,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
    NormalSide,
)

from .dataset import FY_END, FY_ENTRIES, FY_START, SeedEntry, account_name, normal_side

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


# ── 仕訳帳 / 総勘定元帳 (Issue #19) ─────────────────────────────────────────────
# Same dual-path cross-check as the trial balance: a pure reduction of the in-memory
# dataset (used to generate the golden, no DB) and a DB-backed read through the
# production repositories (checked against golden by the pytest harness). The dataset
# is POSTED on load, so both default to 記帳確定 over the full fiscal year.


def _counter_codes(entry: SeedEntry, code: str) -> list[str]:
    """The 相手科目コード of ``entry`` for the ledger account ``code`` (dedup, order kept).

    Mirrors :meth:`ai_books.db.repository.LedgerRepository._counter_accounts`: the other
    accounts in the same 伝票, in line order, with duplicates collapsed.
    """
    counters: list[str] = []
    for line in entry.lines:
        if line.account_code != code and line.account_code not in counters:
            counters.append(line.account_code)
    return counters


def journal_book_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> JournalBook:
    """Reduce the in-memory dataset into a 仕訳帳 — no database required.

    Entries are already in 取引日 → 伝票番号 order; each is POSTED (as the loader stores it).
    """
    book_entries: list[JournalBookEntry] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for entry in entries:
        lines: list[JournalBookLine] = []
        for line in entry.lines:
            lines.append(
                JournalBookLine(
                    account_code=line.account_code,
                    account_name=account_name(line.account_code),
                    side=line.side,
                    amount=line.amount,
                )
            )
            if line.side is EntrySide.DEBIT:
                total_debit += line.amount
            else:
                total_credit += line.amount
        book_entries.append(
            JournalBookEntry(
                entry_date=entry.entry_date,
                voucher_no=entry.voucher_no,
                description=entry.description,
                status=EntryStatus.POSTED,
                lines=lines,
            )
        )
    return JournalBook(
        start_date=FY_START,
        end_date=FY_END,
        status=EntryStatus.POSTED,
        entries=book_entries,
        total_debit=total_debit,
        total_credit=total_credit,
    )


def general_ledger_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> GeneralLedger:
    """Reduce the in-memory dataset into a 総勘定元帳 — no database required.

    For each account (科目コード順) the lines that touch it are collected in chronological
    order and run through the shared :func:`ai_books.ledger.build_ledger_rows`, so the
    running balance matches the production read path exactly. Opening balances are zero
    (the year opens with the 期首残高 伝票, dated on ``FY_START``).
    """
    codes = sorted({line.account_code for entry in entries for line in entry.lines})
    accounts: list[GeneralLedgerAccount] = []
    for code in codes:
        normal = normal_side(code)
        raw_lines: list[ledger.RawLedgerLine] = []
        for index, entry in enumerate(entries):
            counters = _counter_codes(entry, code)
            for line_no, line in enumerate(entry.lines, start=1):
                if line.account_code != code:
                    continue
                raw_lines.append(
                    ledger.RawLedgerLine(
                        entry_id=index,
                        line_no=line_no,
                        entry_date=entry.entry_date,
                        voucher_no=entry.voucher_no,
                        description=entry.description,
                        line_description=None,
                        counter_accounts=counters,
                        side=line.side,
                        amount=line.amount,
                    )
                )
        rows, closing = ledger.build_ledger_rows(raw_lines, normal, Decimal(0))
        accounts.append(
            GeneralLedgerAccount(
                code=code,
                name=account_name(code),
                normal_balance=normal,
                opening_balance=Decimal(0),
                closing_balance=closing,
                rows=[
                    GeneralLedgerRow(
                        entry_date=row.entry_date,
                        voucher_no=row.voucher_no,
                        description=row.description,
                        line_description=row.line_description,
                        counter_accounts=row.counter_accounts,
                        side=row.side,
                        amount=row.amount,
                        running_balance=row.running_balance,
                    )
                    for row in rows
                ],
            )
        )
    return GeneralLedger(
        start_date=FY_START, end_date=FY_END, status=EntryStatus.POSTED, accounts=accounts
    )


def journal_book_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> JournalBook:
    """Read the 仕訳帳 from Postgres through the production :class:`JournalRepository`."""
    return JournalRepository(conn).journal_book(start_date=FY_START, end_date=FY_END, status=status)


def general_ledger_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> GeneralLedger:
    """Read the 総勘定元帳 from Postgres through the production :class:`LedgerRepository`."""
    return LedgerRepository(conn).general_ledger(start=FY_START, end=FY_END, status=status)

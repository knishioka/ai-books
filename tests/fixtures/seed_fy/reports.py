"""Reports over the synthetic year — pure reducers vs. the production aggregation engine.

The golden harness compares two independent computations of the *same* report:

* a pure-Python reducer over :data:`~tests.fixtures.seed_fy.dataset.FY_ENTRIES` (no database) —
  :func:`trial_balance_from_dataset` / :func:`monthly_trend_from_dataset`. These generate the
  committed golden files.
* the **production** aggregation engine (:class:`ai_books.db.repository.LedgerRepository`) run
  over the same data after it has been loaded into Postgres — :func:`trial_balance_from_db` /
  :func:`monthly_trend_from_db`. These are what the pytest harness checks against golden.

The two paths sum independently (a Python ``dict`` reduction vs. a SQL ``GROUP BY``) but share
the one signing rule in :func:`ai_books.ledger.balance_from_totals`, so a mismatch surfaces a
storage/aggregation bug (Decimal lost, a line dropped, a sign flipped) rather than a tautology.
Routing the DB side through the production engine means the golden test *is* the acceptance
check for Issue #18's 集計エンジン — there is no second SQL implementation to drift (#18 calls
for 二重実装を避ける).

A trial balance is the foundation every later report (#20 PL / #21 BS / #22 精算表 / #23 決算書)
builds on, so it is the first — and most reusable — golden snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ai_books import aggregation, ledger
from ai_books.models import (
    EntrySide,
    EntryStatus,
    MonthlyTrend,
    NormalSide,
    TrialBalance,
    TrialBalanceRow,
)

from .dataset import (
    FISCAL_YEAR,
    FY_END,
    FY_ENTRIES,
    FY_START,
    SeedEntry,
    account_name,
    normal_side,
)

if TYPE_CHECKING:
    import psycopg


def _assemble(
    debit_by_code: dict[str, Decimal],
    credit_by_code: dict[str, Decimal],
    name_of: dict[str, str],
    normal_of: dict[str, NormalSide],
) -> TrialBalance:
    """Build a :class:`~ai_books.models.TrialBalance` from per-code sums, ordered by code."""
    rows: list[TrialBalanceRow] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for code in sorted(debit_by_code.keys() | credit_by_code.keys()):
        debit = debit_by_code.get(code, Decimal(0))
        credit = credit_by_code.get(code, Decimal(0))
        balance = ledger.balance_from_totals(debit, credit, normal_of[code])
        rows.append(
            TrialBalanceRow(
                code=code,
                name=name_of[code],
                normal_balance=normal_of[code],
                debit_total=debit,
                credit_total=credit,
                balance=balance,
            )
        )
        total_debit += debit
        total_credit += credit
    return TrialBalance(rows=rows, total_debit=total_debit, total_credit=total_credit)


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
    """Aggregate ``journal_lines`` into a trial balance via the production engine.

    Delegates straight to :meth:`ai_books.db.repository.LedgerRepository.trial_balance` —
    the very code Issue #18 ships — so there is no second SQL implementation to drift
    (#18 calls for 二重実装を避ける). Independent of :func:`trial_balance_from_dataset`
    (SQL ``GROUP BY`` vs. in-memory reduction), so a divergence surfaces a storage or
    aggregation bug, and the golden test doubles as #18's acceptance check. ``status``
    filters which entries are summed (default: 記帳確定 only).
    """
    from ai_books.db.repository import LedgerRepository

    return LedgerRepository(conn).trial_balance(status=status)


# ── 月次推移 (monthly trend) ──────────────────────────────────────────────────────
#: The accounts the monthly-trend golden snapshot fixes. Chosen to exercise both normal
#: sides and movement spread across the year: 普通預金 (most active asset), 売掛金 (掛売上
#: then 回収), 売上高 (credit-normal revenue), 地代家賃 (expense + 期末家事按分).
MONTHLY_TREND_ACCOUNTS: tuple[str, ...] = ("1141", "1160", "4110", "7250")


def monthly_trend_from_dataset(
    code: str, entries: tuple[SeedEntry, ...] = FY_ENTRIES
) -> MonthlyTrend:
    """Reduce the in-memory dataset into one account's 月次推移 — no database required.

    Sums the account's lines per calendar month and carries the balance forward through
    the production tiling/​signing helpers (:func:`ai_books.aggregation.month_windows` /
    :func:`~ai_books.aggregation.build_monthly_trend_points`), so the offline golden source
    and the DB path agree on *structure* while summing independently. The opening balance
    is whatever the account carried in from before 期首 (zero for every account in this
    dataset, since the 期首残高 仕訳 itself falls on 期首).
    """
    normal = normal_side(code)
    open_debit = Decimal(0)
    open_credit = Decimal(0)
    debit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        for line in entry.lines:
            if line.account_code != code:
                continue
            if entry.entry_date < FY_START:
                if line.side is EntrySide.DEBIT:
                    open_debit += line.amount
                else:
                    open_credit += line.amount
                continue
            if entry.entry_date > FY_END:
                continue
            month_start = date(entry.entry_date.year, entry.entry_date.month, 1)
            month_bucket = debit_by_month if line.side is EntrySide.DEBIT else credit_by_month
            month_bucket[month_start] += line.amount

    amounts_by_month = {
        month: aggregation.MonthAmounts(
            debit_total=debit_by_month.get(month, Decimal(0)),
            credit_total=credit_by_month.get(month, Decimal(0)),
        )
        for month in debit_by_month.keys() | credit_by_month.keys()
    }
    opening_balance = ledger.balance_from_totals(open_debit, open_credit, normal)
    windows = aggregation.month_windows(FY_START, FY_END)
    points, closing_balance = aggregation.build_monthly_trend_points(
        windows, amounts_by_month, normal, opening_balance
    )
    return MonthlyTrend(
        account_id=0,  # offline reduction has no DB id; the snapshot omits it
        code=code,
        name=account_name(code),
        normal_balance=normal,
        fiscal_year=FISCAL_YEAR,
        start_date=FY_START,
        end_date=FY_END,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        points=points,
    )


def monthly_trend_from_db(
    conn: psycopg.Connection[Any], code: str, *, status: EntryStatus | None = EntryStatus.POSTED
) -> MonthlyTrend:
    """Compute one account's 月次推移 from the DB via the production engine.

    Resolves the account and the :data:`~tests.fixtures.seed_fy.dataset.FISCAL_YEAR` row
    (seeded by :func:`~tests.fixtures.seed_fy.loader.load_fiscal_year`) and delegates to
    :meth:`ai_books.db.repository.LedgerRepository.monthly_trend` — the code Issue #18 ships.
    """
    from ai_books.db.repository import (
        AccountRepository,
        FiscalYearRepository,
        LedgerRepository,
    )

    account = AccountRepository(conn).get_by_code(code)
    assert account is not None, f"account {code} not seeded"
    assert account.id is not None
    year = FiscalYearRepository(conn).get_by_name(FISCAL_YEAR)
    assert year is not None, f"fiscal year {FISCAL_YEAR} not seeded"
    return LedgerRepository(conn).monthly_trend(
        account.id,
        fiscal_year=year.name,
        start=year.start_date,
        end=year.end_date,
        status=status,
    )

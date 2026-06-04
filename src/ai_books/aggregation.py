"""Pure aggregation arithmetic — the 集計エンジン behind trial balance and 月次推移.

Like :mod:`ai_books.ledger`, this module is kept free of SQL and I/O so the *rules*
(how a fiscal year splits into accounting months, how per-month sums roll up into a
carried-forward balance, how a column of debit/credit footings becomes a signed trial
balance) are unit tested without a database. :class:`~ai_books.db.repository.LedgerRepository`
supplies the summed rows; this module turns them into the typed result models.

Every balance is signed the one way the whole codebase signs balances — through
:func:`ai_books.ledger.balance_from_totals` — so the trial balance, the 月次推移 running
balance, and :func:`ai_books.db.repository.LedgerRepository.account_balance` can never
disagree about which side an account's normal balance sits on.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from ai_books import ledger
from ai_books.models import (
    AccountType,
    MonthlyTrendPoint,
    NormalSide,
    TrialBalance,
    TrialBalanceRow,
    Worksheet,
    WorksheetRow,
)

#: Account types that land on the 損益計算書 (P/L) side of the 精算表 (収益 / 費用);
#: every other type (資産 / 負債 / 純資産) lands on the 貸借対照表 (B/S) side.
_PL_ACCOUNT_TYPES = frozenset({AccountType.REVENUE, AccountType.EXPENSE})


class AccountTotals(NamedTuple):
    """An account's summed footings over a window, before signing into a balance."""

    code: str
    name: str
    normal_balance: NormalSide
    debit_total: Decimal
    credit_total: Decimal


class WorksheetAccount(NamedTuple):
    """An account's footings split into operating vs. 期末整理 (adjustment) buckets.

    ``unadjusted_*`` are the gross 借方 / 貸方 footings of every *non*-adjustment entry
    (the 残高試算表 source); ``adjustment_*`` are the gross footings of the account's 期末
    整理仕訳 (the 修正記入 columns). :func:`assemble_worksheet` nets and routes these into
    the eight worksheet columns.
    """

    code: str
    name: str
    account_type: AccountType
    unadjusted_debit: Decimal
    unadjusted_credit: Decimal
    adjustment_debit: Decimal
    adjustment_credit: Decimal


class MonthWindow(NamedTuple):
    """One accounting month clamped to the fiscal year it belongs to.

    ``month_start`` is always the first day of the calendar month (the key the SQL
    ``date_trunc('month', …)`` bucket uses), while ``start``/``end`` are clamped to the
    fiscal year — so a fiscal year that begins or ends mid-month still tiles cleanly.
    """

    label: str  # 'YYYY-MM'
    month_start: date  # 月初 (calendar, the grouping key)
    start: date  # 会計期間にクランプした月の開始
    end: date  # 会計期間にクランプした月の終了


class MonthAmounts(NamedTuple):
    """Summed 借方 / 貸方 amounts posted within a single month."""

    debit_total: Decimal
    credit_total: Decimal


def _last_day_of_month(year: int, month: int) -> date:
    """Return the last calendar day of ``year``-``month``."""
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first_next - timedelta(days=1)


def month_windows(start: date, end: date) -> list[MonthWindow]:
    """Tile ``[start, end]`` (inclusive) into one :class:`MonthWindow` per calendar month.

    Each month that the fiscal year touches yields exactly one window, in chronological
    order, with the first and last clamped to ``start`` / ``end``. A fiscal year spanning a
    calendar-year boundary (e.g. an April→March year) is handled by counting months, not
    by assuming the start month is January.
    """
    if end < start:
        raise ValueError(f"end {end} must not be before start {start}")
    windows: list[MonthWindow] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        month_start = date(year, month, 1)
        month_end = _last_day_of_month(year, month)
        windows.append(
            MonthWindow(
                label=f"{year:04d}-{month:02d}",
                month_start=month_start,
                start=max(month_start, start),
                end=min(month_end, end),
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return windows


def assemble_trial_balance(
    totals: list[AccountTotals],
    *,
    as_of: date | None = None,
    start_date: date | None = None,
) -> TrialBalance:
    """Sign each account's footings into a balance and sum the two column totals.

    ``totals`` is expected pre-ordered (the repository orders by 勘定科目コード); the order
    is preserved so the output is stable. The column footings are the plain sums of the
    per-account ``debit_total`` / ``credit_total`` — equal iff the books balance (借貸平均).
    """
    rows: list[TrialBalanceRow] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for item in totals:
        balance = ledger.balance_from_totals(
            item.debit_total, item.credit_total, item.normal_balance
        )
        rows.append(
            TrialBalanceRow(
                code=item.code,
                name=item.name,
                normal_balance=item.normal_balance,
                debit_total=item.debit_total,
                credit_total=item.credit_total,
                balance=balance,
            )
        )
        total_debit += item.debit_total
        total_credit += item.credit_total
    return TrialBalance(
        as_of=as_of,
        start_date=start_date,
        rows=rows,
        total_debit=total_debit,
        total_credit=total_credit,
    )


def build_monthly_trend_points(
    windows: list[MonthWindow],
    amounts_by_month: dict[date, MonthAmounts],
    normal: NormalSide,
    opening_balance: Decimal,
) -> tuple[list[MonthlyTrendPoint], Decimal]:
    """Roll per-month sums into trend points with a carried-forward balance.

    Walks ``windows`` in order; for each month it signs that month's footings into a
    ``net_change`` (via :func:`ai_books.ledger.balance_from_totals`) and advances the
    running balance from ``opening_balance``. A month with no activity yields a point
    with zero movement and the balance unchanged — so the series stays tiled across the
    whole fiscal year. Returns the points and the closing balance (期末残高).
    """
    running = opening_balance
    points: list[MonthlyTrendPoint] = []
    for window in windows:
        amounts = amounts_by_month.get(window.month_start, MonthAmounts(Decimal(0), Decimal(0)))
        net_change = ledger.balance_from_totals(amounts.debit_total, amounts.credit_total, normal)
        running += net_change
        points.append(
            MonthlyTrendPoint(
                month=window.label,
                start_date=window.start,
                end_date=window.end,
                debit_total=amounts.debit_total,
                credit_total=amounts.credit_total,
                net_change=net_change,
                closing_balance=running,
            )
        )
    return points, running


def _split_net(net: Decimal) -> tuple[Decimal, Decimal]:
    """Place a debit-positive net balance on a worksheet column-pair (借方, 貸方).

    A positive net is a debit balance (borne in the 借方 column); a negative net is a
    credit balance (its absolute value in the 貸方 column); zero leaves both columns at
    zero. This is what lets a contra account (期末商品棚卸高 等) cross to the opposite side.
    """
    if net > 0:
        return net, Decimal(0)
    if net < 0:
        return Decimal(0), -net
    return Decimal(0), Decimal(0)


def assemble_worksheet(
    accounts: list[WorksheetAccount],
    *,
    fiscal_year: str,
    start_date: date,
    end_date: date,
) -> Worksheet:
    """Build the 精算表 from each account's operating + 期末整理 footings.

    Per account: the *unadjusted* net (借方 - 貸方 of non-adjustment entries) fills the
    残高試算表 columns; the 期末整理仕訳 footings fill the 修正記入 columns gross; the
    *adjusted* net (unadjusted + adjustment) is routed to the 損益計算書欄 (収益 / 費用) or
    the 貸借対照表欄 (資産 / 負債 / 純資産) by ``account_type``, placed on the side its sign
    falls on. ``accounts`` is expected pre-ordered (the repository orders by 勘定科目コード);
    the order is preserved. ``net_income`` (当期純利益) is the P/L footing difference
    (収益計 - 費用計); it equals the B/S footing difference exactly when the books balance.
    """
    rows: list[WorksheetRow] = []
    trial_debit_total = Decimal(0)
    trial_credit_total = Decimal(0)
    adjustment_debit_total = Decimal(0)
    adjustment_credit_total = Decimal(0)
    pl_debit_total = Decimal(0)
    pl_credit_total = Decimal(0)
    bs_debit_total = Decimal(0)
    bs_credit_total = Decimal(0)

    for account in accounts:
        unadjusted_net = account.unadjusted_debit - account.unadjusted_credit
        trial_debit, trial_credit = _split_net(unadjusted_net)

        adjusted_net = unadjusted_net + account.adjustment_debit - account.adjustment_credit
        col_debit, col_credit = _split_net(adjusted_net)
        is_pl = account.account_type in _PL_ACCOUNT_TYPES
        pl_debit = col_debit if is_pl else Decimal(0)
        pl_credit = col_credit if is_pl else Decimal(0)
        bs_debit = Decimal(0) if is_pl else col_debit
        bs_credit = Decimal(0) if is_pl else col_credit

        rows.append(
            WorksheetRow(
                code=account.code,
                name=account.name,
                account_type=account.account_type,
                trial_debit=trial_debit,
                trial_credit=trial_credit,
                adjustment_debit=account.adjustment_debit,
                adjustment_credit=account.adjustment_credit,
                pl_debit=pl_debit,
                pl_credit=pl_credit,
                bs_debit=bs_debit,
                bs_credit=bs_credit,
            )
        )
        trial_debit_total += trial_debit
        trial_credit_total += trial_credit
        adjustment_debit_total += account.adjustment_debit
        adjustment_credit_total += account.adjustment_credit
        pl_debit_total += pl_debit
        pl_credit_total += pl_credit
        bs_debit_total += bs_debit
        bs_credit_total += bs_credit

    return Worksheet(
        fiscal_year=fiscal_year,
        start_date=start_date,
        end_date=end_date,
        rows=rows,
        trial_debit_total=trial_debit_total,
        trial_credit_total=trial_credit_total,
        adjustment_debit_total=adjustment_debit_total,
        adjustment_credit_total=adjustment_credit_total,
        pl_debit_total=pl_debit_total,
        pl_credit_total=pl_credit_total,
        bs_debit_total=bs_debit_total,
        bs_credit_total=bs_credit_total,
        net_income=pl_credit_total - pl_debit_total,
    )

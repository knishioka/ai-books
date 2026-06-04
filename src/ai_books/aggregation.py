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
    ProfitAndLoss,
    ProfitAndLossLine,
    ProfitAndLossSection,
    StatementCategory,
    TrialBalance,
    TrialBalanceRow,
)


class AccountTotals(NamedTuple):
    """An account's summed footings over a window, before signing into a balance."""

    code: str
    name: str
    normal_balance: NormalSide
    debit_total: Decimal
    credit_total: Decimal


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


class PlAccountTotals(NamedTuple):
    """An account's summed footings over the fiscal year, with its 区分 / 表示区分.

    Carries enough to place the account on the P/L: its ``account_type`` decides whether
    it belongs to the 損益計算書 at all (収益 / 費用), and ``statement_category`` which 段階
    section it rolls up under (``None`` ⇒ 未分類, surfaced rather than dropped).
    """

    code: str
    name: str
    account_type: AccountType
    statement_category: StatementCategory | None
    normal_balance: NormalSide
    debit_total: Decimal
    credit_total: Decimal


#: The P/L 段階表示, in order: each section is ``(key, 日本語ラベル, 表示区分…)``. 製造原価
#: (材料費 / 労務費 / 製造経費) folds into 売上原価 for the *PL本体*; the full 製造原価報告書
#: breakout is #23. Every P/L-relevant 表示区分 appears here exactly once, so a 収益/費用 account
#: whose 区分 is missing here lands in 未分類 (網羅性の検出).
_PL_SECTIONS: tuple[tuple[str, str, tuple[StatementCategory, ...]], ...] = (
    ("sales", "売上高", (StatementCategory.SALES,)),
    (
        "cost_of_goods_sold",
        "売上原価",
        (
            StatementCategory.COST_OF_GOODS_SOLD,
            StatementCategory.MANUFACTURING_MATERIALS,
            StatementCategory.MANUFACTURING_LABOR,
            StatementCategory.MANUFACTURING_OVERHEAD,
        ),
    ),
    ("selling_admin_expenses", "販売費及び一般管理費", (StatementCategory.SELLING_ADMIN_EXPENSES,)),
    ("non_operating_income", "営業外収益", (StatementCategory.NON_OPERATING_INCOME,)),
    ("non_operating_expenses", "営業外費用", (StatementCategory.NON_OPERATING_EXPENSES,)),
)

#: 表示区分 → section key, derived from :data:`_PL_SECTIONS` (the single source of the grouping).
_PL_CATEGORY_SECTION: dict[StatementCategory, str] = {
    category: key for key, _label, categories in _PL_SECTIONS for category in categories
}

#: Account types that belong on the 損益計算書 (everything else is a 貸借対照表 account, #21).
_PL_ACCOUNT_TYPES = frozenset({AccountType.REVENUE, AccountType.EXPENSE})


def assemble_profit_and_loss(
    accounts: list[PlAccountTotals],
    *,
    fiscal_year: str,
    start_date: date,
    end_date: date,
) -> ProfitAndLoss:
    """Group 収益/費用 account footings into the staged 損益計算書 (PL本体).

    Each account is signed into a balance the one way the codebase signs balances
    (:func:`ai_books.ledger.balance_from_totals`) and bucketed by 表示区分; non-P/L accounts
    (資産 / 負債 / 純資産) are skipped, and a 収益/費用 account with no P/L 区分 is collected
    into ``unclassified`` so the gap is visible (網羅性). The 段階利益 are then pure
    subtractions of the section subtotals, so they reconcile with the trial balance.

    ``accounts`` is expected pre-ordered by 勘定科目コード (the repository orders that way);
    the order is preserved within each section so the output is stable.
    """
    section_lines: dict[str, list[ProfitAndLossLine]] = {key: [] for key, *_ in _PL_SECTIONS}
    unclassified: list[ProfitAndLossLine] = []
    for item in accounts:
        if item.account_type not in _PL_ACCOUNT_TYPES:
            continue  # 貸借対照表 account — belongs to the balance sheet (#21), not the P/L
        balance = ledger.balance_from_totals(
            item.debit_total, item.credit_total, item.normal_balance
        )
        line = ProfitAndLossLine(
            code=item.code,
            name=item.name,
            category=item.statement_category,
            amount=balance,
        )
        section_key = (
            _PL_CATEGORY_SECTION.get(item.statement_category)
            if item.statement_category is not None
            else None
        )
        if section_key is None:
            unclassified.append(line)
        else:
            section_lines[section_key].append(line)

    sections = {
        key: ProfitAndLossSection(
            key=key,
            label=label,
            lines=section_lines[key],
            subtotal=sum((line.amount for line in section_lines[key]), Decimal(0)),
        )
        for key, label, _categories in _PL_SECTIONS
    }
    sales = sections["sales"]
    cost_of_goods_sold = sections["cost_of_goods_sold"]
    selling_admin_expenses = sections["selling_admin_expenses"]
    non_operating_income = sections["non_operating_income"]
    non_operating_expenses = sections["non_operating_expenses"]

    gross_profit = sales.subtotal - cost_of_goods_sold.subtotal
    operating_income = gross_profit - selling_admin_expenses.subtotal
    ordinary_income = (
        operating_income + non_operating_income.subtotal - non_operating_expenses.subtotal
    )
    net_income = ordinary_income  # 特別損益なし (個人事業の青色申告決算書 PL本体)

    return ProfitAndLoss(
        fiscal_year=fiscal_year,
        start_date=start_date,
        end_date=end_date,
        sales=sales,
        cost_of_goods_sold=cost_of_goods_sold,
        gross_profit=gross_profit,
        selling_admin_expenses=selling_admin_expenses,
        operating_income=operating_income,
        non_operating_income=non_operating_income,
        non_operating_expenses=non_operating_expenses,
        ordinary_income=ordinary_income,
        net_income=net_income,
        unclassified=unclassified,
    )

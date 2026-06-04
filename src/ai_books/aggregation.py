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
    STATEMENT_CATEGORY_ACCOUNT_TYPE,
    AccountType,
    BalanceSheet,
    BalanceSheetLine,
    BalanceSheetSection,
    DepreciationLine,
    DepreciationSchedule,
    EntryStatus,
    FinancialStatements,
    ManufacturingCost,
    ManufacturingCostLine,
    ManufacturingCostSection,
    MonthlySalesPurchases,
    MonthlySalesPurchasesRow,
    MonthlyTrendPoint,
    NormalSide,
    ProfitAndLoss,
    ProfitAndLossLine,
    ProfitAndLossSection,
    StatementCategory,
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


class ClassifiedBalance(NamedTuple):
    """An account's signed balance tagged with its 表示区分, ready to roll up onto a 決算書.

    ``balance`` is already in 正常残高方向 (the output of :func:`ai_books.ledger.balance_from_totals`,
    i.e. a 試算表 row's balance); ``statement_category`` decides which 区分 it lands in (B/S
    sections) or whether it feeds 当期純利益 (P/L categories).
    """

    code: str
    name: str
    statement_category: StatementCategory
    balance: Decimal


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


#: The B/S 表示区分 in statement layout order: 資産 (流動→固定), 負債 (流動→固定), 純資産.
_ASSET_CATEGORIES: tuple[StatementCategory, ...] = (
    StatementCategory.CURRENT_ASSETS,
    StatementCategory.FIXED_ASSETS,
)
_LIABILITY_CATEGORIES: tuple[StatementCategory, ...] = (
    StatementCategory.CURRENT_LIABILITIES,
    StatementCategory.FIXED_LIABILITIES,
)
_EQUITY_CATEGORIES: tuple[StatementCategory, ...] = (StatementCategory.EQUITY,)


def _balance_sheet_sections(
    by_category: dict[StatementCategory, list[BalanceSheetLine]],
    categories: tuple[StatementCategory, ...],
) -> tuple[list[BalanceSheetSection], Decimal]:
    """Build the sections for one side of the B/S and return them with their grand total.

    Every requested 区分 yields a section (even with no lines) so the statement layout is
    complete and stable; the section subtotal foots its lines and the returned total foots
    the sections.
    """
    sections: list[BalanceSheetSection] = []
    side_total = Decimal(0)
    for category in categories:
        lines = by_category.get(category, [])
        subtotal = sum((line.balance for line in lines), Decimal(0))
        sections.append(BalanceSheetSection(category=category, lines=lines, subtotal=subtotal))
        side_total += subtotal
    return sections, side_total


def assemble_balance_sheet(
    balances: list[ClassifiedBalance],
    *,
    as_of: date | None = None,
    status: EntryStatus | None = None,
) -> BalanceSheet:
    """Roll classified account balances into a 貸借対照表 (資産 = 負債 + 純資産, 当期純利益 込).

    Each balance is dispatched by its 表示区分: B/S categories (資産/負債/純資産) become section
    lines, while P/L categories feed 当期純利益 (収益 add, 費用 subtract — the same figure the
    損益計算書 #20 reports). 当期純利益 is folded into 純資産合計 so the equation closes. A B/S account
    that nets to exactly zero is dropped (no standing balance to report); a non-zero balance is
    kept and displayed as-is, including the rare 正常残高 と逆 (negative) case. ``balances`` is
    expected pre-ordered by 勘定科目コード (a 試算表 is), and that order is preserved within sections.
    """
    by_category: dict[StatementCategory, list[BalanceSheetLine]] = {}
    net_income = Decimal(0)
    for item in balances:
        account_type = STATEMENT_CATEGORY_ACCOUNT_TYPE[item.statement_category]
        if account_type is AccountType.REVENUE:
            net_income += item.balance
        elif account_type is AccountType.EXPENSE:
            net_income -= item.balance
        elif item.balance != 0:  # 資産 / 負債 / 純資産 — drop accounts that net to zero
            by_category.setdefault(item.statement_category, []).append(
                BalanceSheetLine(code=item.code, name=item.name, balance=item.balance)
            )

    assets, total_assets = _balance_sheet_sections(by_category, _ASSET_CATEGORIES)
    liabilities, total_liabilities = _balance_sheet_sections(by_category, _LIABILITY_CATEGORIES)
    equity, equity_accounts_total = _balance_sheet_sections(by_category, _EQUITY_CATEGORIES)
    return BalanceSheet(
        as_of=as_of,
        status=status,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        net_income=net_income,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=equity_accounts_total + net_income,
    )


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


# ── 青色申告決算書 (financial statements, Issue #23) ──────────────────────────────
# Composes the already-assembled 損益計算書 (#20) と 貸借対照表 (#21) with the 内訳 the
# 決算書 form carries: 月別売上(収入)・仕入 / 減価償却費の計算 / 製造原価の計算. Every breakdown is
# derived from the same journal/勘定科目 data (no 固定資産 / 従業員 マスタ), and reconciled back to
# the PL/BS by :attr:`FinancialStatements.is_consistent`.

#: The 勘定科目名 of the 減価償却費 accounts (経費 7210 / 製造経費 6330 share this name). Summing
#: the PL lines with this name gives the expense-side 償却費 the 減価償却費の計算 must foot to.
DEPRECIATION_ACCOUNT_NAME = "減価償却費"

#: 月別仕入金額 sums the 仕入 accounts (仕入高 / 原材料仕入高) — those whose 科目名 ends with this
#: suffix, which the 棚卸高 振替 accounts (期首/期末商品・原材料棚卸高) deliberately do not.
PURCHASE_ACCOUNT_NAME_SUFFIX = "仕入高"

#: 製造原価 区分 in form order: ``(section key, 日本語ラベル, 表示区分)``.
_MANUFACTURING_SECTIONS: tuple[tuple[str, str, StatementCategory], ...] = (
    ("materials", "材料費", StatementCategory.MANUFACTURING_MATERIALS),
    ("labor", "労務費", StatementCategory.MANUFACTURING_LABOR),
    ("overhead", "製造経費", StatementCategory.MANUFACTURING_OVERHEAD),
)


class FixedAssetTotals(NamedTuple):
    """One 固定資産勘定's footings, ready to become a 減価償却費の計算 row (直接法).

    ``acquisition_cost`` is the account's 累計借方 up to 期末 (取得価額 = 期首簿価 + 当期取得),
    ``depreciation_expense`` the 当期の貸方 (当期減少額 — 直接法では償却費), and ``closing_book_value``
    the 期末簿価 in 正常残高方向 (= the account's 貸借対照表 上の残高).
    """

    code: str
    name: str
    acquisition_cost: Decimal
    depreciation_expense: Decimal
    closing_book_value: Decimal


def is_purchase_account_name(name: str) -> bool:
    """True when ``name`` is a 仕入 account (仕入高 / 原材料仕入高), not a 棚卸高 振替 account."""
    return name.endswith(PURCHASE_ACCOUNT_NAME_SUFFIX)


def _assemble_manufacturing_cost(profit_and_loss: ProfitAndLoss) -> ManufacturingCost:
    """Regroup the PL本体's 製造原価科目 (folded into 売上原価) into the 製造原価の計算.

    Reads the 売上原価 lines tagged with a 製造原価 表示区分 (材料費 / 労務費 / 製造経費), keeping
    them in 科目コード順, so the breakdown sums to exactly the 製造 portion of 売上原価 by sharing
    its source lines. 当期製品製造原価 equals 当期製造費用 (no 仕掛品 account is seeded).
    """
    lines_by_category: dict[StatementCategory, list[ManufacturingCostLine]] = {
        category: [] for _key, _label, category in _MANUFACTURING_SECTIONS
    }
    for line in profit_and_loss.cost_of_goods_sold.lines:
        if line.category in lines_by_category:
            lines_by_category[line.category].append(
                ManufacturingCostLine(code=line.code, name=line.name, amount=line.amount)
            )

    sections: dict[str, ManufacturingCostSection] = {}
    total = Decimal(0)
    for key, label, category in _MANUFACTURING_SECTIONS:
        lines = lines_by_category[category]
        subtotal = sum((line.amount for line in lines), Decimal(0))
        sections[key] = ManufacturingCostSection(
            key=key, label=label, lines=lines, subtotal=subtotal
        )
        total += subtotal

    return ManufacturingCost(
        materials=sections["materials"],
        labor=sections["labor"],
        overhead=sections["overhead"],
        total_manufacturing_cost=total,
        cost_of_goods_manufactured=total,  # 当期製造費用 + 期首仕掛品 - 期末仕掛品 (仕掛品なし)
    )


def _assemble_monthly_sales_purchases(
    start: date,
    end: date,
    sales_by_month: dict[date, MonthAmounts],
    purchases_by_month: dict[date, MonthAmounts],
) -> MonthlySalesPurchases:
    """Tile [start, end] into 12 月 of 売上(収入)金額 and 仕入金額, with the column footings.

    Reuses :func:`month_windows` so the fiscal year tiles the same way the 月次推移 does; each
    month's 売上 is signed credit-normal (収益) and 仕入 debit-normal (費用) via the one signing
    rule, so a quiet month carries zeros and ``sales_total`` foots to the PL's 売上高.
    """
    rows: list[MonthlySalesPurchasesRow] = []
    sales_total = Decimal(0)
    purchases_total = Decimal(0)
    for window in month_windows(start, end):
        sales_amounts = sales_by_month.get(window.month_start, MonthAmounts(Decimal(0), Decimal(0)))
        purchase_amounts = purchases_by_month.get(
            window.month_start, MonthAmounts(Decimal(0), Decimal(0))
        )
        sales = ledger.balance_from_totals(
            sales_amounts.debit_total, sales_amounts.credit_total, NormalSide.CREDIT
        )
        purchases = ledger.balance_from_totals(
            purchase_amounts.debit_total, purchase_amounts.credit_total, NormalSide.DEBIT
        )
        rows.append(MonthlySalesPurchasesRow(month=window.label, sales=sales, purchases=purchases))
        sales_total += sales
        purchases_total += purchases
    return MonthlySalesPurchases(
        rows=rows, sales_total=sales_total, purchases_total=purchases_total
    )


def _depreciation_expense_total(profit_and_loss: ProfitAndLoss) -> Decimal:
    """Σ of the PL's 減価償却費 lines (経費 + 製造経費) — the expense-side 償却費 to foot to."""
    sections = (
        profit_and_loss.cost_of_goods_sold,
        profit_and_loss.selling_admin_expenses,
        profit_and_loss.non_operating_expenses,
    )
    return sum(
        (
            line.amount
            for section in sections
            for line in section.lines
            if line.name == DEPRECIATION_ACCOUNT_NAME
        ),
        Decimal(0),
    )


def _assemble_depreciation_schedule(
    profit_and_loss: ProfitAndLoss, fixed_assets: list[FixedAssetTotals]
) -> DepreciationSchedule:
    """Build the 減価償却費の計算 from each 固定資産's 当期減少額, footed against PL 減価償却費.

    Only 固定資産 that recorded 当期償却 (当期の貸方 ≠ 0) become rows — 非償却資産 (土地 等) and
    untouched assets are left out, matching the form. ``expense_total`` is the PL's 減価償却費, so
    :attr:`DepreciationSchedule.is_consistent` checks the asset-side 償却費 equals the expense side
    (固定資産データと整合). ``fixed_assets`` is expected pre-ordered by 勘定科目コード.
    """
    lines = [
        DepreciationLine(
            code=asset.code,
            name=asset.name,
            acquisition_cost=asset.acquisition_cost,
            depreciation_expense=asset.depreciation_expense,
            closing_book_value=asset.closing_book_value,
        )
        for asset in fixed_assets
        if asset.depreciation_expense != 0
    ]
    total = sum((line.depreciation_expense for line in lines), Decimal(0))
    return DepreciationSchedule(
        lines=lines,
        total_depreciation=total,
        expense_total=_depreciation_expense_total(profit_and_loss),
    )


def assemble_financial_statements(
    *,
    fiscal_year: str,
    start_date: date,
    end_date: date,
    profit_and_loss: ProfitAndLoss,
    balance_sheet: BalanceSheet,
    sales_by_month: dict[date, MonthAmounts],
    purchases_by_month: dict[date, MonthAmounts],
    fixed_assets: list[FixedAssetTotals],
) -> FinancialStatements:
    """Compose the 青色申告決算書 from the assembled PL/BS and the journal-derived 内訳.

    The 損益計算書 (#20) and 貸借対照表 (#21) are carried verbatim; the 内訳 are built here from the
    same books — 製造原価 regroups the PL's 売上原価 製造原価科目, 月別売上・仕入 tiles the
    per-month footings, and 減価償却 reads each 固定資産's 当期減少額. Every breakdown is reconciled
    back to the PL/BS by :attr:`FinancialStatements.is_consistent`, so the composed object either
    ties out end to end or surfaces exactly where it does not.
    """
    return FinancialStatements(
        fiscal_year=fiscal_year,
        start_date=start_date,
        end_date=end_date,
        profit_and_loss=profit_and_loss,
        monthly=_assemble_monthly_sales_purchases(
            start_date, end_date, sales_by_month, purchases_by_month
        ),
        depreciation=_assemble_depreciation_schedule(profit_and_loss, fixed_assets),
        balance_sheet=balance_sheet,
        manufacturing_cost=_assemble_manufacturing_cost(profit_and_loss),
    )

"""Pure (no-DB) tests for the aggregation engine (Issue #18).

Exercise the calendar-month tiling, the trial-balance assembly, and the carried-forward
monthly-trend roll-up in isolation from SQL — plus the model invariants (借貸平均 and
期首残高 + Σ期中増減 = 期末残高) the DB-backed tests then check end to end. These run
everywhere, including ``./scripts/verify.sh`` without a live Postgres.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ai_books import aggregation
from ai_books.models import (
    AccountType,
    MonthlyTrend,
    NormalSide,
    ProfitAndLoss,
    StatementCategory,
)

# --- month_windows ------------------------------------------------------------


def test_month_windows_tiles_a_calendar_year() -> None:
    windows = aggregation.month_windows(date(2025, 1, 1), date(2025, 12, 31))
    assert [w.label for w in windows] == [f"2025-{m:02d}" for m in range(1, 13)]
    assert windows[0].month_start == date(2025, 1, 1)
    assert windows[0].start == date(2025, 1, 1)
    assert windows[-1].end == date(2025, 12, 31)


def test_month_windows_spans_calendar_year_boundary() -> None:
    # An April→March fiscal year must tile by counting months, not assuming January.
    windows = aggregation.month_windows(date(2025, 4, 1), date(2026, 3, 31))
    assert len(windows) == 12
    assert windows[0].label == "2025-04"
    assert windows[-1].label == "2026-03"
    assert windows[-1].month_start == date(2026, 3, 1)


def test_month_windows_clamps_partial_first_and_last_months() -> None:
    windows = aggregation.month_windows(date(2025, 1, 15), date(2025, 3, 10))
    assert [w.label for w in windows] == ["2025-01", "2025-02", "2025-03"]
    # First/last clamp to the fiscal bounds; the grouping key stays the 1st of the month.
    assert windows[0].start == date(2025, 1, 15)
    assert windows[0].month_start == date(2025, 1, 1)
    assert windows[-1].end == date(2025, 3, 10)


def test_month_windows_single_month() -> None:
    windows = aggregation.month_windows(date(2025, 6, 1), date(2025, 6, 30))
    assert len(windows) == 1
    assert windows[0].label == "2025-06"


def test_month_windows_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="must not be before"):
        aggregation.month_windows(date(2025, 6, 1), date(2025, 5, 1))


# --- assemble_trial_balance ---------------------------------------------------


def test_assemble_trial_balance_signs_each_side_and_foots_columns() -> None:
    totals = [
        aggregation.AccountTotals(
            "1110", "現金", NormalSide.DEBIT, Decimal("1000"), Decimal("200")
        ),
        aggregation.AccountTotals(
            "4110", "売上高", NormalSide.CREDIT, Decimal("100"), Decimal("500")
        ),
    ]
    tb = aggregation.assemble_trial_balance(totals, as_of=date(2025, 12, 31))

    by_code = {row.code: row for row in tb.rows}
    assert by_code["1110"].balance == Decimal("800")  # debit-normal: 1000 - 200
    assert by_code["4110"].balance == Decimal("400")  # credit-normal: 500 - 100
    assert tb.total_debit == Decimal("1100")
    assert tb.total_credit == Decimal("700")
    assert tb.as_of == date(2025, 12, 31)
    assert not tb.is_balanced  # the made-up totals do not foot


def test_assemble_trial_balance_is_balanced_when_columns_match() -> None:
    totals = [
        aggregation.AccountTotals("1110", "現金", NormalSide.DEBIT, Decimal("500"), Decimal("0")),
        aggregation.AccountTotals(
            "4110", "売上高", NormalSide.CREDIT, Decimal("0"), Decimal("500")
        ),
    ]
    tb = aggregation.assemble_trial_balance(totals)
    assert tb.is_balanced
    assert tb.total_debit == tb.total_credit == Decimal("500")


def test_assemble_trial_balance_preserves_input_order() -> None:
    totals = [
        aggregation.AccountTotals("4110", "売上高", NormalSide.CREDIT, Decimal(0), Decimal(1)),
        aggregation.AccountTotals("1110", "現金", NormalSide.DEBIT, Decimal(1), Decimal(0)),
    ]
    tb = aggregation.assemble_trial_balance(totals)
    assert [row.code for row in tb.rows] == ["4110", "1110"]


# --- build_monthly_trend_points -----------------------------------------------


def test_build_monthly_trend_points_carries_balance_forward() -> None:
    windows = aggregation.month_windows(date(2025, 1, 1), date(2025, 3, 31))
    amounts = {
        date(2025, 1, 1): aggregation.MonthAmounts(Decimal("1000"), Decimal("0")),
        date(2025, 3, 1): aggregation.MonthAmounts(Decimal("0"), Decimal("400")),
    }
    points, closing = aggregation.build_monthly_trend_points(
        windows, amounts, NormalSide.DEBIT, opening_balance=Decimal("100")
    )

    assert [p.net_change for p in points] == [Decimal("1000"), Decimal("0"), Decimal("-400")]
    # Running balance: 100 → 1100 → 1100 (quiet month) → 700.
    assert [p.closing_balance for p in points] == [
        Decimal("1100"),
        Decimal("1100"),
        Decimal("700"),
    ]
    assert closing == Decimal("700")
    # A quiet month still appears with zero movement (会計期間で正しく区切られる).
    assert points[1].debit_total == points[1].credit_total == Decimal("0")


def test_build_monthly_trend_points_credit_normal_increases_on_credit() -> None:
    windows = aggregation.month_windows(date(2025, 1, 1), date(2025, 1, 31))
    amounts = {date(2025, 1, 1): aggregation.MonthAmounts(Decimal("0"), Decimal("250"))}
    points, closing = aggregation.build_monthly_trend_points(
        windows, amounts, NormalSide.CREDIT, opening_balance=Decimal("0")
    )
    assert points[0].net_change == Decimal("250")  # credit-normal grows on the credit side
    assert closing == Decimal("250")


# --- model invariants ---------------------------------------------------------


def _trend(opening: str, monthly_net: list[str], normal: NormalSide) -> MonthlyTrend:
    windows = aggregation.month_windows(date(2025, 1, 1), date(2025, 12, 31))
    # Encode each month's net as a one-sided amount so build_* signs it back to that net.
    amounts: dict[date, aggregation.MonthAmounts] = {}
    for window, net in zip(windows, monthly_net, strict=True):
        value = Decimal(net)
        if (value >= 0) == (normal is NormalSide.DEBIT):
            amounts[window.month_start] = aggregation.MonthAmounts(abs(value), Decimal(0))
        else:
            amounts[window.month_start] = aggregation.MonthAmounts(Decimal(0), abs(value))
    points, closing = aggregation.build_monthly_trend_points(
        windows, amounts, normal, Decimal(opening)
    )
    return MonthlyTrend(
        account_id=1,
        code="1110",
        name="現金",
        normal_balance=normal,
        fiscal_year="FY2025",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        opening_balance=Decimal(opening),
        closing_balance=closing,
        points=points,
    )


def test_monthly_trend_is_consistent_when_balance_reconciles() -> None:
    trend = _trend("100", ["50"] + ["0"] * 11, NormalSide.DEBIT)
    assert trend.net_change == Decimal("50")
    assert trend.closing_balance == Decimal("150")
    assert trend.is_consistent  # 期首 100 + Σ増減 50 = 期末 150


def test_monthly_trend_inconsistent_when_closing_tampered() -> None:
    trend = _trend("100", ["50"] + ["0"] * 11, NormalSide.DEBIT)
    tampered = trend.model_copy(update={"closing_balance": Decimal("999")})
    assert not tampered.is_consistent


# --- assemble_profit_and_loss -------------------------------------------------


def _pl_account(
    code: str,
    account_type: AccountType,
    category: StatementCategory | None,
    debit: str,
    credit: str,
) -> aggregation.PlAccountTotals:
    return aggregation.PlAccountTotals(
        code=code,
        name=code,
        account_type=account_type,
        statement_category=category,
        normal_balance=NormalSide.DEBIT
        if account_type in (AccountType.ASSET, AccountType.EXPENSE)
        else NormalSide.CREDIT,
        debit_total=Decimal(debit),
        credit_total=Decimal(credit),
    )


def _assemble(accounts: list[aggregation.PlAccountTotals]) -> ProfitAndLoss:
    return aggregation.assemble_profit_and_loss(
        accounts, fiscal_year="FY2025", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
    )


def test_profit_and_loss_stages_reconcile() -> None:
    pl = _assemble(
        [
            _pl_account("4110", AccountType.REVENUE, StatementCategory.SALES, "0", "1000"),
            _pl_account(
                "5120", AccountType.EXPENSE, StatementCategory.COST_OF_GOODS_SOLD, "300", "0"
            ),
            _pl_account(
                "5130", AccountType.EXPENSE, StatementCategory.COST_OF_GOODS_SOLD, "0", "50"
            ),  # 期末棚卸 (貸方) → 売上原価を控除
            _pl_account(
                "7250", AccountType.EXPENSE, StatementCategory.SELLING_ADMIN_EXPENSES, "200", "0"
            ),
            _pl_account(
                "8110", AccountType.REVENUE, StatementCategory.NON_OPERATING_INCOME, "0", "30"
            ),
            _pl_account(
                "8210", AccountType.EXPENSE, StatementCategory.NON_OPERATING_EXPENSES, "10", "0"
            ),
        ]
    )
    assert pl.sales.subtotal == Decimal("1000")
    assert pl.cost_of_goods_sold.subtotal == Decimal("250")  # 300 - 50
    assert pl.gross_profit == Decimal("750")  # 1000 - 250
    assert pl.operating_income == Decimal("550")  # 750 - 200
    assert pl.ordinary_income == Decimal("570")  # 550 + 30 - 10
    assert pl.net_income == Decimal("570")
    assert pl.is_consistent
    assert pl.unclassified == []


def test_profit_and_loss_folds_manufacturing_into_cost_of_goods_sold() -> None:
    pl = _assemble(
        [
            _pl_account(
                "5120", AccountType.EXPENSE, StatementCategory.COST_OF_GOODS_SOLD, "100", "0"
            ),
            _pl_account(
                "6120", AccountType.EXPENSE, StatementCategory.MANUFACTURING_MATERIALS, "300", "0"
            ),
            _pl_account(
                "6210", AccountType.EXPENSE, StatementCategory.MANUFACTURING_LABOR, "400", "0"
            ),
            _pl_account(
                "6330", AccountType.EXPENSE, StatementCategory.MANUFACTURING_OVERHEAD, "240", "0"
            ),
        ]
    )
    # 製造原価 (材料/労務/製造経費) rolls into 売上原価 for the PL本体 (#23 breaks it out).
    assert pl.cost_of_goods_sold.subtotal == Decimal("1040")
    assert {line.code for line in pl.cost_of_goods_sold.lines} == {"5120", "6120", "6210", "6330"}


def test_profit_and_loss_detects_unclassified_revenue_expense() -> None:
    # AC: 表示区分への科目集約が網羅的 — a 収益/費用 account with no 表示区分 is surfaced,
    # not silently dropped into a subtotal.
    pl = _assemble(
        [
            _pl_account("4110", AccountType.REVENUE, StatementCategory.SALES, "0", "1000"),
            _pl_account("9999", AccountType.EXPENSE, None, "500", "0"),
        ]
    )
    assert [line.code for line in pl.unclassified] == ["9999"]
    assert pl.cost_of_goods_sold.subtotal == Decimal("0")
    # The unclassified 500 is excluded from every subtotal/段階利益 so the gap stays visible.
    assert pl.net_income == Decimal("1000")


def test_profit_and_loss_skips_balance_sheet_accounts() -> None:
    # 資産/負債/純資産 accounts never appear on the P/L (they belong to the B/S, #21).
    pl = _assemble(
        [
            _pl_account("4110", AccountType.REVENUE, StatementCategory.SALES, "0", "1000"),
            _pl_account("1110", AccountType.ASSET, StatementCategory.CURRENT_ASSETS, "5000", "0"),
            _pl_account("3110", AccountType.EQUITY, StatementCategory.EQUITY, "0", "5000"),
        ]
    )
    assert pl.unclassified == []
    assert pl.sales.subtotal == Decimal("1000")
    assert pl.net_income == Decimal("1000")


# --- assemble_balance_sheet ---------------------------------------------------


def _bs(code: str, category: StatementCategory, balance: str) -> aggregation.ClassifiedBalance:
    return aggregation.ClassifiedBalance(
        code=code, name=code, statement_category=category, balance=Decimal(balance)
    )


def test_assemble_balance_sheet_groups_sections_and_balances() -> None:
    # 資産 = 負債 + 純資産 (当期純利益 込) over a minimal hand-built set.
    balance_sheet = aggregation.assemble_balance_sheet(
        [
            _bs("1110", StatementCategory.CURRENT_ASSETS, "1000"),
            _bs("2510", StatementCategory.FIXED_LIABILITIES, "400"),
            _bs("3110", StatementCategory.EQUITY, "300"),
            # a 収益 feeds 当期純利益 (300), folded into 純資産: 300 equity + 300 net = 600
            _bs("4110", StatementCategory.SALES, "300"),
        ]
    )
    assert balance_sheet.total_assets == Decimal("1000")
    assert balance_sheet.total_liabilities == Decimal("400")
    assert balance_sheet.net_income == Decimal("300")
    assert balance_sheet.total_equity == Decimal("600")
    assert balance_sheet.is_balanced


def test_assemble_balance_sheet_fails_loud_on_unplaced_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A B/S 表示区分 carrying a balance but not assigned to any section must raise rather than
    # be silently dropped (which would quietly break 貸借一致). Simulate a future category by
    # shrinking the handled set so CURRENT_ASSETS is no longer placed.
    monkeypatch.setattr(
        aggregation,
        "_BALANCE_SHEET_CATEGORIES",
        aggregation._BALANCE_SHEET_CATEGORIES - {StatementCategory.CURRENT_ASSETS},
    )
    with pytest.raises(ValueError, match="not assigned to any section"):
        aggregation.assemble_balance_sheet([_bs("1110", StatementCategory.CURRENT_ASSETS, "1000")])

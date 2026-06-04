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
from ai_books.models import AccountType, MonthlyTrend, NormalSide, Worksheet

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


# --- assemble_worksheet -------------------------------------------------------


def _wsa(
    code: str,
    name: str,
    account_type: AccountType,
    unadjusted_debit: str,
    unadjusted_credit: str,
    adjustment_debit: str = "0",
    adjustment_credit: str = "0",
) -> aggregation.WorksheetAccount:
    return aggregation.WorksheetAccount(
        code=code,
        name=name,
        account_type=account_type,
        unadjusted_debit=Decimal(unadjusted_debit),
        unadjusted_credit=Decimal(unadjusted_credit),
        adjustment_debit=Decimal(adjustment_debit),
        adjustment_credit=Decimal(adjustment_credit),
    )


def _worksheet(accounts: list[aggregation.WorksheetAccount]) -> Worksheet:
    return aggregation.assemble_worksheet(
        accounts,
        fiscal_year="FY2025",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    )


def test_assemble_worksheet_routes_columns_and_reconciles_profit() -> None:
    # 機械装置 carries an adjustment (減価償却), so the 試算表 → 修正記入 → BS flow is exercised.
    accounts = [
        _wsa("1110", "現金", AccountType.ASSET, "1000", "0"),
        _wsa("1530", "機械装置", AccountType.ASSET, "500", "0", adjustment_credit="100"),
        _wsa("3110", "元入金", AccountType.EQUITY, "0", "500"),
        _wsa("4110", "売上高", AccountType.REVENUE, "0", "1000"),
        _wsa("6330", "減価償却費", AccountType.EXPENSE, "0", "0", adjustment_debit="100"),
    ]
    ws = _worksheet(accounts)
    by_code = {row.code: row for row in ws.rows}

    # 残高試算表欄: unadjusted net placed on its side; 修正記入欄: the adjustment gross.
    assert by_code["1530"].trial_debit == Decimal("500")
    assert by_code["1530"].adjustment_credit == Decimal("100")
    # 機械装置 adjusted 500 - 100 = 400 lands in the B/S (asset) debit column.
    assert by_code["1530"].bs_debit == Decimal("400")
    assert by_code["1530"].pl_debit == by_code["1530"].pl_credit == Decimal("0")
    # 売上高 routes to the P/L credit column; 減価償却費 (adjustment only) to the P/L debit.
    assert by_code["4110"].pl_credit == Decimal("1000")
    assert by_code["6330"].pl_debit == Decimal("100")
    assert by_code["6330"].trial_debit == by_code["6330"].trial_credit == Decimal("0")

    # 当期純利益 reconciles across both panels (収益 1000 - 費用 100 = 900) — the 自己検算.
    assert ws.net_income == Decimal("900")
    assert ws.pl_net_income == ws.bs_net_income == Decimal("900")
    assert ws.is_consistent


def test_assemble_worksheet_handles_contra_and_loss() -> None:
    # 期末商品棚卸高 is an expense account carrying a credit balance (売上原価の控除): it must
    # cross to the P/L *credit* column, and the year is a 純損失.
    accounts = [
        _wsa("1110", "現金", AccountType.ASSET, "100", "0"),
        _wsa("1180", "商品", AccountType.ASSET, "0", "0", adjustment_debit="300"),
        _wsa("2120", "買掛金", AccountType.LIABILITY, "0", "800"),
        _wsa("4110", "売上高", AccountType.REVENUE, "0", "100"),
        _wsa("5120", "仕入高", AccountType.EXPENSE, "800", "0"),
        _wsa("5130", "期末商品棚卸高", AccountType.EXPENSE, "0", "0", adjustment_credit="300"),
    ]
    ws = _worksheet(accounts)
    by_code = {row.code: row for row in ws.rows}

    # contra: expense account, but the credit balance lands on the P/L credit side.
    assert by_code["5130"].pl_credit == Decimal("300")
    assert by_code["5130"].pl_debit == Decimal("0")
    assert by_code["1180"].bs_debit == Decimal("300")  # adjusted-in asset

    # Loss: 費用 800 > 収益 (100 + 300 contra) = 400, so 当期純利益 is negative and still reconciles.
    assert ws.net_income == Decimal("-400")
    assert ws.pl_net_income == ws.bs_net_income == Decimal("-400")
    assert ws.is_trial_balanced
    assert ws.is_adjustments_balanced
    assert ws.is_consistent


def test_assemble_worksheet_preserves_order_and_is_empty_safe() -> None:
    ws = _worksheet([])
    assert ws.rows == []
    assert ws.net_income == Decimal("0")
    assert ws.is_consistent  # 0 == 0 == 0

    ordered = _worksheet(
        [
            _wsa("4110", "売上高", AccountType.REVENUE, "0", "1"),
            _wsa("1110", "現金", AccountType.ASSET, "1", "0"),
        ]
    )
    assert [row.code for row in ordered.rows] == ["4110", "1110"]


def test_assemble_worksheet_inconsistent_when_books_do_not_balance() -> None:
    # An unbalanced input (no offsetting credit) breaks the 当期純利益 self-check.
    ws = _worksheet([_wsa("1110", "現金", AccountType.ASSET, "100", "0")])
    assert ws.pl_net_income != ws.bs_net_income
    assert not ws.is_consistent

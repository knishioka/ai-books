"""Pure (no-DB) tests for the synthetic fiscal year and the golden harness.

These run everywhere — including ``./scripts/verify.sh`` without a live Postgres —
because they exercise only the in-memory dataset and the offline reducer. They cover
the acceptance criteria that don't need a round-trip: the dataset balances overall,
the committed golden file is up to date, and golden files are rewritten *only* through
the explicit ``--update`` path. The DB-backed half lives in ``test_seed_fy_db.py``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ai_books import aggregation
from ai_books.models import AccountType, EntrySide
from ai_books.reports import (
    agricultural_income_snapshot,
    balance_sheet_snapshot,
    financial_statements_snapshot,
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    real_estate_income_snapshot,
    worksheet_snapshot,
)
from tests.fixtures.seed_fy import (
    AG_ENTRIES,
    FY_END,
    FY_ENTRIES,
    FY_START,
    MONTHLY_TREND_ACCOUNTS,
    RE_ENTRIES,
    SeedEntry,
    SeedLine,
    agricultural_income_from_dataset,
    balance_sheet_from_dataset,
    diff_snapshots,
    financial_statements_from_dataset,
    general_ledger_from_dataset,
    journal_book_from_dataset,
    load_golden,
    monthly_trend_from_dataset,
    monthly_trend_snapshot,
    profit_and_loss_from_dataset,
    real_estate_income_from_dataset,
    trial_balance_from_dataset,
    trial_balance_snapshot,
    validate_dataset,
    worksheet_from_dataset,
)
from tests.fixtures.seed_fy import golden as golden_mod
from tests.fixtures.seed_fy.dataset import account_type


def _row(snapshot: dict[str, Any], code: str) -> dict[str, Any]:
    """The single snapshot row for ``code`` (fails the test if absent)."""
    rows: list[dict[str, Any]] = [r for r in snapshot["rows"] if r["code"] == code]
    assert rows, f"no row for {code} in snapshot"
    return rows[0]


def test_dataset_is_internally_consistent() -> None:
    # AC: dataset validates (unique vouchers, known codes, every entry balanced).
    validate_dataset()


def test_dataset_books_balance_overall() -> None:
    # AC: 借貸が全体でバランスする — debit and credit column footings are equal.
    trial_balance = trial_balance_from_dataset()
    assert trial_balance.is_balanced
    assert trial_balance.total_debit == trial_balance.total_credit == Decimal("10791500")


def test_validate_dataset_detects_imbalance() -> None:
    # An entry whose lines don't balance must be rejected before it can reach the DB.
    broken = SeedEntry(
        "X-001",
        FY_ENTRIES[0].entry_date,
        "imbalanced",
        (
            SeedLine("1110", EntrySide.DEBIT, Decimal("100")),
            SeedLine("4110", EntrySide.CREDIT, Decimal("90")),
        ),
    )
    with pytest.raises(ValueError, match="借方"):
        validate_dataset((broken,))


def test_validate_dataset_detects_unknown_code() -> None:
    bad = SeedEntry(
        "X-002",
        FY_ENTRIES[0].entry_date,
        "unknown code",
        (
            SeedLine("9999", EntrySide.DEBIT, Decimal("100")),
            SeedLine("4110", EntrySide.CREDIT, Decimal("100")),
        ),
    )
    with pytest.raises(ValueError, match="unknown account code"):
        validate_dataset((bad,))


def test_key_balances_are_hand_traceable() -> None:
    # Anchors the README's worked figures: each derives from a handful of round entries.
    snapshot = trial_balance_snapshot(trial_balance_from_dataset())
    assert _row(snapshot, "4110")["balance"] == "1650000.00"  # 売上高 = 220k+550k+880k
    assert _row(snapshot, "1110")["balance"] == "300000.00"  # 現金 200k+220k-80k-40k
    assert _row(snapshot, "1160")["balance"] == "880000.00"  # 売掛金 (期末未回収)
    assert _row(snapshot, "7250")["balance"] == "360000.00"  # 地代家賃 600k - 家事按分240k
    assert _row(snapshot, "5130")["balance"] == "-350000.00"  # 期末商品棚卸高 (控除/貸方)
    assert _row(snapshot, "2120")["balance"] == "0.00"  # 買掛金 全額決済済


def test_committed_golden_file_is_up_to_date() -> None:
    # AC: the harness runs from pytest and the committed golden matches the dataset.
    fresh = trial_balance_snapshot(trial_balance_from_dataset())
    committed = load_golden("trial_balance")
    problems = diff_snapshots(committed, fresh)
    assert problems == [], (
        "golden/trial_balance.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update`:\n  - " + "\n  - ".join(problems)
    )


def test_committed_monthly_trend_golden_is_up_to_date() -> None:
    # AC (#18): the 月次推移 golden matches the dataset reduction (offline source of truth).
    fresh = monthly_trend_snapshot([monthly_trend_from_dataset(c) for c in MONTHLY_TREND_ACCOUNTS])
    committed = load_golden("monthly_trend")
    problems = diff_snapshots(committed, fresh)
    assert problems == [], (
        "golden/monthly_trend.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update monthly_trend`:\n  - " + "\n  - ".join(problems)
    )


def test_monthly_trend_is_consistent_and_partitioned_by_month() -> None:
    # AC (#18): 月次推移が会計期間で正しく区切られる + 期首残高 + Σ期中増減 = 期末残高.
    for code in MONTHLY_TREND_ACCOUNTS:
        trend = monthly_trend_from_dataset(code)
        assert len(trend.points) == 12, f"{code}: FY2025 should tile into 12 months"
        assert [p.month for p in trend.points] == [f"2025-{m:02d}" for m in range(1, 13)]
        assert trend.is_consistent, f"{code}: opening + Σ net must equal closing"


def test_monthly_trend_closing_matches_trial_balance() -> None:
    # The 期末残高 of each trend must equal that account's trial-balance 残高 (one truth).
    tb_rows = {row.code: row for row in trial_balance_from_dataset().rows}
    for code in MONTHLY_TREND_ACCOUNTS:
        trend = monthly_trend_from_dataset(code)
        assert trend.closing_balance == tb_rows[code].balance, f"{code}: 期末残高 != 試算表残高"


def test_diff_snapshots_pinpoints_the_changed_account() -> None:
    base = trial_balance_snapshot(trial_balance_from_dataset())
    mutated = {**base, "rows": [dict(r) for r in base["rows"]]}
    mutated["rows"][0]["balance"] = "999.00"
    code = mutated["rows"][0]["code"]
    problems = diff_snapshots(base, mutated)
    assert any(code in problem and "balance" in problem for problem in problems)


def test_committed_journal_book_golden_is_up_to_date() -> None:
    # AC (#19): 仕訳帳 output matches the frozen golden, generated from the dataset.
    fresh = journal_book_snapshot(journal_book_from_dataset())
    problems = diff_snapshots(load_golden("journal_book"), fresh)
    assert problems == [], "golden/journal_book.json is stale:\n  - " + "\n  - ".join(problems)


def test_committed_general_ledger_golden_is_up_to_date() -> None:
    # AC (#19): 総勘定元帳 output matches the frozen golden, generated from the dataset.
    fresh = general_ledger_snapshot(general_ledger_from_dataset())
    problems = diff_snapshots(load_golden("general_ledger"), fresh)
    assert problems == [], "golden/general_ledger.json is stale:\n  - " + "\n  - ".join(problems)


def test_committed_profit_and_loss_golden_is_up_to_date() -> None:
    # AC (#20): 出力が #17 の golden と一致 — the P/L snapshot matches the frozen golden.
    fresh = profit_and_loss_snapshot(profit_and_loss_from_dataset())
    problems = diff_snapshots(load_golden("profit_and_loss"), fresh)
    assert problems == [], (
        "golden/profit_and_loss.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update profit_and_loss`:\n  - "
        + "\n  - ".join(problems)
    )


def test_profit_and_loss_stages_reconcile_with_trial_balance() -> None:
    # AC (#20): PL の各段階利益が試算表と整合 (売上総利益・営業利益・経常利益・当期純利益).
    pl = profit_and_loss_from_dataset()
    assert pl.is_consistent

    # 当期純利益 must equal Σ収益 - Σ費用 taken independently from the trial balance.
    tb = trial_balance_from_dataset()
    revenue = sum(
        (row.balance for row in tb.rows if account_type(row.code) is AccountType.REVENUE),
        Decimal(0),
    )
    expense = sum(
        (row.balance for row in tb.rows if account_type(row.code) is AccountType.EXPENSE),
        Decimal(0),
    )
    assert pl.net_income == revenue - expense == Decimal("-580500")
    # The staged figures the README/golden fix.
    assert pl.gross_profit == Decimal("160000")  # 売上1,650,000 - 売上原価1,490,000
    assert pl.operating_income == Decimal("-560000")
    assert pl.ordinary_income == Decimal("-580500")


def test_profit_and_loss_classifies_every_revenue_expense_account() -> None:
    # AC (#20): 表示区分への科目集約が網羅的 — the synthetic year leaves nothing 未分類.
    pl = profit_and_loss_from_dataset()
    assert pl.unclassified == []
    # 製造原価 folds into 売上原価 for the PL本体 (#23 produces the 製造原価報告書).
    cogs_codes = {line.code for line in pl.cost_of_goods_sold.lines}
    assert {"6120", "6210", "6330"} <= cogs_codes  # 材料費 / 労務費 / 製造経費
    assert pl.cost_of_goods_sold.subtotal == Decimal("1490000")


def test_journal_book_is_chronological_and_complete() -> None:
    # AC (#19): 仕訳帳が日付順・伝票番号順で全件出力される.
    book = journal_book_from_dataset()
    keys = [(e.entry_date, e.voucher_no) for e in book.entries]
    assert keys == sorted(keys)  # 取引日 → 伝票番号 order
    assert len(book.entries) == len(FY_ENTRIES)  # 全件
    assert book.is_balanced
    assert book.total_debit == book.total_credit == Decimal("10791500")


def test_general_ledger_running_balance_is_hand_traceable() -> None:
    # AC (#19): 総勘定元帳が科目別に累計残高付きで出力される.
    ledger = general_ledger_from_dataset()
    cash = next(a for a in ledger.accounts if a.code == "1110")
    # 現金: 期首200,000 → +売上220,000 → -旅費80,000 → -消耗品40,000.
    assert [r.running_balance for r in cash.rows] == [
        Decimal("200000"),
        Decimal("420000"),
        Decimal("340000"),
        Decimal("300000"),
    ]
    assert cash.opening_balance == Decimal("0")
    assert cash.closing_balance == Decimal("300000")
    # 諸口: the opening 期首残高 伝票 has several counter accounts on the cash row.
    assert cash.rows[0].counter_accounts == ["1141", "1180", "1530", "2510", "3110"]
    assert cash.rows[0].voucher_no == "FY2025-000"


def test_committed_worksheet_golden_is_up_to_date() -> None:
    # AC (#22): 精算表 output matches the frozen golden, generated from the dataset.
    fresh = worksheet_snapshot(worksheet_from_dataset())
    problems = diff_snapshots(load_golden("worksheet"), fresh)
    assert problems == [], (
        "golden/worksheet.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update worksheet`:\n  - " + "\n  - ".join(problems)
    )


def test_worksheet_columns_are_self_balancing() -> None:
    # AC (#22): 精算表の各列合計が整合 — 試算表 / 修正記入 each foot, and 当期純利益 agrees
    # between the 損益計算書欄 and 貸借対照表欄 (精算表の自己検算).
    ws = worksheet_from_dataset()
    assert ws.is_trial_balanced
    assert ws.is_adjustments_balanced
    assert ws.pl_net_income == ws.bs_net_income == ws.net_income
    assert ws.is_consistent
    # FY2025 closes at a 純損失 of 580,500 (収益 2,000,500 - 費用 2,581,000).
    assert ws.net_income == Decimal("-580500")


def test_worksheet_splits_year_end_adjustments_into_their_own_column() -> None:
    # AC (#22): 期末整理仕訳 (減価償却 / 棚卸 / 家事按分) show as 修正記入, not in the 残高試算表.
    rows = {row.code: row for row in worksheet_from_dataset().rows}

    # 地代家賃: 試算表 600,000, then 家事按分 240,000 in 修正記入 → 損益計算書欄 360,000.
    rent = rows["7250"]
    assert rent.trial_debit == Decimal("600000")
    assert rent.adjustment_credit == Decimal("240000")
    assert rent.pl_debit == Decimal("360000")
    # 商品: 期首 300,000 試算表, 期末整理 (期首振替 300,000 貸 + 期末計上 350,000 借) → BS 350,000.
    goods = rows["1180"]
    assert goods.trial_debit == Decimal("300000")
    assert goods.adjustment_debit == Decimal("350000")
    assert goods.adjustment_credit == Decimal("300000")
    assert goods.bs_debit == Decimal("350000")
    # 期末商品棚卸高: a 売上原価 contra booked only at 期末 → 損益計算書欄 *credit* (控除).
    closing_stock = rows["5130"]
    assert closing_stock.trial_debit == closing_stock.trial_credit == Decimal("0")
    assert closing_stock.adjustment_credit == Decimal("350000")
    assert closing_stock.pl_credit == Decimal("350000")


def test_committed_balance_sheet_golden_is_up_to_date() -> None:
    # AC (#21): 貸借対照表 output matches the frozen golden, generated from the dataset.
    fresh = balance_sheet_snapshot(balance_sheet_from_dataset())
    problems = diff_snapshots(load_golden("balance_sheet"), fresh)
    assert problems == [], "golden/balance_sheet.json is stale:\n  - " + "\n  - ".join(problems)


def test_balance_sheet_balances() -> None:
    # AC (#21): 資産合計 = 負債 + 純資産 が常に成立 (seed で検証).
    balance_sheet = balance_sheet_from_dataset()
    assert balance_sheet.is_balanced
    assert balance_sheet.total_assets == Decimal("3319500")
    assert balance_sheet.total_liabilities + balance_sheet.total_equity == Decimal("3319500")


def test_balance_sheet_net_income_matches_trial_balance() -> None:
    # AC (#21): 当期純利益 が PL と一致 — until #20 lands, anchor it to the P/L accounts of
    # the 試算表 (収益残高 - 費用残高), which is exactly what the 損益計算書 will report.
    rows = {row.code: row for row in trial_balance_from_dataset().rows}
    revenue = rows["4110"].balance + rows["8110"].balance  # 売上高 + 受取利息
    expense = sum(
        (rows[c].balance for c in ("5110", "5120", "5130", "6120", "6210", "6330")),
        Decimal(0),
    ) + sum(
        (rows[c].balance for c in ("7130", "7140", "7150", "7200", "7210", "7250", "8210")),
        Decimal(0),
    )
    expected_net = revenue - expense
    assert expected_net == Decimal("-580500")  # FY2025 は損失年度
    assert balance_sheet_from_dataset().net_income == expected_net


def test_balance_sheet_shows_sole_proprietor_equity_items() -> None:
    # AC (#21): 個人事業の純資産項目 (元入金 / 事業主勘定) が正しく表示される.
    balance_sheet = balance_sheet_from_dataset()
    equity_lines = {line.code: line for section in balance_sheet.equity for line in section.lines}
    assert equity_lines["3110"].balance == Decimal("3200000")  # 元入金
    assert equity_lines["3120"].balance == Decimal("100000")  # 事業主借
    # 事業主貸 (家事按分) sits in 流動資産 for a 個人事業 B/S, not 純資産.
    asset_codes = {line.code for section in balance_sheet.assets for line in section.lines}
    assert "1290" in asset_codes


def test_balance_sheet_omits_zero_balance_accounts() -> None:
    # 買掛金 (2120) nets to zero (全額決済済) and so has no standing balance to report.
    balance_sheet = balance_sheet_from_dataset()
    all_codes = {
        line.code
        for side in (balance_sheet.assets, balance_sheet.liabilities, balance_sheet.equity)
        for section in side
        for line in section.lines
    }
    assert "2120" not in all_codes


def test_golden_updates_only_via_explicit_flag(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC: 誤上書き防止 — without --update nothing is written; with it, the file appears.
    monkeypatch.setattr(golden_mod, "GOLDEN_DIR", tmp_path)
    path = tmp_path / "trial_balance.json"

    # Dry-run against a missing golden: reports stale, writes nothing, exits non-zero.
    assert golden_mod.main([]) == 1
    assert not path.exists()

    # Explicit update creates it; a following dry-run is clean.
    assert golden_mod.main(["--update"]) == 0
    assert path.exists()
    assert golden_mod.main([]) == 0


# --- 青色申告決算書 (Issue #23) ------------------------------------------------


def test_committed_financial_statements_golden_is_up_to_date() -> None:
    # AC (#23): 出力が #17 の golden と一致 — the 決算書 snapshot matches the frozen golden.
    fresh = financial_statements_snapshot(financial_statements_from_dataset())
    problems = diff_snapshots(load_golden("financial_statements"), fresh)
    assert problems == [], (
        "golden/financial_statements.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update financial_statements`:\n  - "
        + "\n  - ".join(problems)
    )


def test_financial_statements_breakdowns_reconcile_with_pl_and_bs() -> None:
    # AC (#23): 各内訳合計が PL/BS と一致 — the composed self-check ties every 内訳 back to the
    # PL/BS, and the staged figures the README fixes hold.
    fs = financial_statements_from_dataset()
    assert fs.is_consistent

    # 月別売上合計 = 売上高, 月別仕入合計 = 仕入高 + 原材料仕入高.
    assert fs.monthly.sales_total == fs.profit_and_loss.sales.subtotal == Decimal("1650000")
    assert fs.monthly.purchases_total == Decimal("900000")
    assert len(fs.monthly.rows) == 12  # tiles the whole fiscal year like the form

    # 製造原価の計算 = 材料費 + 労務費 + 製造経費, and 売上原価 splits into 商品 + 製造.
    assert fs.manufacturing_cost.cost_of_goods_manufactured == Decimal("940000")
    assert fs.merchandise_cost_of_sales == Decimal("550000")
    assert (
        fs.profit_and_loss.cost_of_goods_sold.subtotal
        == fs.merchandise_cost_of_sales + fs.manufacturing_cost.cost_of_goods_manufactured
    )


def test_financial_statements_depreciation_ties_to_fixed_assets_and_pl() -> None:
    # AC (#23): 減価償却費の計算欄が固定資産データと整合 — each 固定資産's 当期償却 foots to the PL
    # 減価償却費 (経費 + 製造経費) and its 期末簿価 equals the 貸借対照表 figure.
    fs = financial_statements_from_dataset()
    depreciation = {line.code: line for line in fs.depreciation.lines}
    # 機械装置 (製造減価償却) と 工具器具備品 (販管減価償却) — 直接法.
    assert depreciation["1530"].acquisition_cost == Decimal("1200000")
    assert depreciation["1530"].depreciation_expense == Decimal("240000")
    assert depreciation["1530"].closing_book_value == Decimal("960000")
    assert depreciation["1550"].depreciation_expense == Decimal("60000")
    # 償却費合計 = PL 減価償却費 (6330 240,000 + 7210 60,000).
    assert fs.depreciation.total_depreciation == fs.depreciation.expense_total == Decimal("300000")
    # 期末簿価 == 貸借対照表 固定資産 残高.
    bs_fixed = {
        line.code: line.balance
        for section in fs.balance_sheet.assets
        if section.category.value == "fixed_assets"
        for line in section.lines
    }
    assert bs_fixed["1530"] == depreciation["1530"].closing_book_value
    assert bs_fixed["1550"] == depreciation["1550"].closing_book_value


# --- 不動産所得 収入側 内訳 (KOA220 data-supply, Issue #124) ----------------------


def test_real_estate_dataset_is_consistent_and_balances() -> None:
    # The landlord dataset is a separate FY2025 scenario; it validates and its books balance.
    validate_dataset(RE_ENTRIES)
    assert trial_balance_from_dataset(RE_ENTRIES).is_balanced


def test_committed_real_estate_income_golden_is_up_to_date() -> None:
    # AC (#124): the 不動産所得 収入側 内訳 golden matches the dataset reduction (offline source).
    fresh = real_estate_income_snapshot(real_estate_income_from_dataset())
    problems = diff_snapshots(load_golden("real_estate_income"), fresh)
    assert problems == [], (
        "golden/real_estate_income.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update real_estate_income`:\n  - "
        + "\n  - ".join(problems)
    )


def test_real_estate_income_breakdowns_reconcile() -> None:
    # AC (#124): 各内訳の計が内訳行と一致 — the composed self-check ties every 計 back to its rows.
    re = real_estate_income_from_dataset()
    assert re.is_consistent
    # 物件ごとの本年中の収入金額 (賃貸料 + 礼金/権利金/更新料 + 名義書換料); 保証金敷金 は収入外.
    by_code = {line.account_code: line for line in re.rental_income_lines}
    assert by_code["4210"].income_subtotal == Decimal("1300000")  # 賃貸料1,200,000 + 礼金100,000
    assert by_code["4220"].income_subtotal == Decimal("960000")  # 賃貸料900,000 + 更新料60,000
    assert re.gross_income == Decimal("2260000")
    assert re.deposit_total == Decimal("200000")  # 保証金敷金 (収入金額には含めない)
    # 地代家賃 / 借入金利子 の内訳.
    assert re.rent_paid_total == re.rent_paid_deductible_total == Decimal("240000")
    assert re.loan_year_end_balance_total == Decimal("7500000")  # 期首8,000,000 - 返済500,000
    assert re.loan_interest_total == re.loan_interest_deductible_total == Decimal("80000")


def test_real_estate_income_total_ties_to_rental_account_balances() -> None:
    # AC (#124): 本年中の収入金額 = 受取家賃 勘定科目残高 — the contract split cannot drift from the
    # books (this is exactly what the dataset↔db dual path then pins independently).
    balances = {row.code: row.balance for row in trial_balance_from_dataset(RE_ENTRIES).rows}
    for line in real_estate_income_from_dataset().rental_income_lines:
        assert line.income_subtotal == balances[line.account_code]


def test_assemble_real_estate_income_rejects_split_that_does_not_foot() -> None:
    # AC (#124): a contract split that does not foot to the 受取家賃 balance is a fail-loud error.
    bad = aggregation.RentalIncomeTotals(
        account_code="4210",
        income_total=Decimal("1300000"),
        property_type="貸家",
        usage="住宅用",
        location="x",
        tenant_address="x",
        tenant_name="x",
        contract_start_month=1,
        contract_end_month=12,
        rent_annual=Decimal("1000000"),  # 内訳合計 1,000,000 != 残高 1,300,000
        key_money=Decimal("0"),
        right_money=Decimal("0"),
        renewal_fee=Decimal("0"),
        name_change_other=Decimal("0"),
        deposit=Decimal("0"),
    )
    with pytest.raises(ValueError, match="受取家賃"):
        aggregation.assemble_real_estate_income(
            [bad], [], [], fiscal_year="FY2025", start_date=FY_START, end_date=FY_END
        )


# --- 農業所得 収入側 内訳 (KOA240 data-supply, Issue #125) ----------------------


def test_agricultural_dataset_is_consistent_and_balances() -> None:
    # The farmer dataset is a separate FY2025 scenario; it validates and its books balance.
    validate_dataset(AG_ENTRIES)
    assert trial_balance_from_dataset(AG_ENTRIES).is_balanced


def test_committed_agricultural_income_golden_is_up_to_date() -> None:
    # AC (#125): the 農業所得 収入側 内訳 golden matches the dataset reduction (offline source).
    fresh = agricultural_income_snapshot(agricultural_income_from_dataset())
    problems = diff_snapshots(load_golden("agricultural_income"), fresh)
    assert problems == [], (
        "golden/agricultural_income.json is stale; regenerate with "
        "`python -m tests.fixtures.seed_fy --update agricultural_income`:\n  - "
        + "\n  - ".join(problems)
    )


def test_agricultural_income_breakdowns_reconcile() -> None:
    # AC (#125): 各内訳の計が内訳行と一致 — the composed self-check ties every 計 back to its rows.
    ag = agricultural_income_from_dataset()
    assert ag.is_consistent
    # 農産物計 (田畑/果樹/特殊施設 を foot) と畜産物その他.
    assert ag.farm_product_sales_total == Decimal("2000000")
    assert ag.livestock_sales_total == Decimal("1800000")
    assert ag.sales_amount_total == Decimal("3800000")
    assert ag.home_consumption_total == Decimal("140000")  # 農産物110,000 + 畜産物30,000
    assert ag.misc_income_total == Decimal("200000")
    # 収入金額 計 = 小計 - 期首棚卸 + 期末棚卸.
    assert ag.subtotal == Decimal("4140000")
    assert ag.opening_inventory_total == Decimal("200000")  # 前年繰越メタ
    assert ag.closing_inventory_total == Decimal("250000")  # 農産物 1185 残高
    assert ag.gross_income == Decimal("4190000")
    # 育成費用の計算 (carried) は内部で整合.
    assert ag.cultivation_subtotal_total == Decimal("510000")
    assert ag.cultivation_carryover_to_next_total == Decimal("200000")
    assert ag.deductible_cultivation_cost == Decimal("155000")


def test_agricultural_income_total_ties_to_account_balances() -> None:
    # AC (#125): 販売金額/家事消費/雑収入/期末棚卸 = 勘定科目残高 — the split cannot drift from the books.
    balances = {row.code: row.balance for row in trial_balance_from_dataset(AG_ENTRIES).rows}
    ag = agricultural_income_from_dataset()
    # 田畑 (4310) / 果樹 (4320) / 特殊施設 (4330) の販売金額計が各売上科目残高に foot する.
    by_account: dict[str, Decimal] = {}
    for line in ag.crop_income_lines:
        by_account[line.account_code] = (
            by_account.get(line.account_code, Decimal(0)) + line.sales_amount
        )
    assert by_account["4310"] == balances["4310"] == Decimal("1100000")
    assert by_account["4320"] == balances["4320"] == Decimal("400000")
    assert by_account["4330"] == balances["4330"] == Decimal("500000")
    assert ag.livestock_sales_total == balances["4340"] == Decimal("1800000")
    assert ag.home_consumption_total == balances["4350"] == Decimal("140000")
    assert ag.misc_income_total == balances["4360"] == Decimal("200000")
    assert ag.closing_inventory_total == balances["1185"] == Decimal("250000")


def test_assemble_agricultural_income_rejects_split_that_does_not_foot() -> None:
    # AC (#125): a category split that does not foot to its 勘定科目 balance is a fail-loud error.
    bad = aggregation.CropIncomeTotals(
        account_code="4310",
        category="田畑",
        crop_name="米",
        planted_area=Decimal("120"),
        harvest_quantity=Decimal("6000"),
        opening_inventory_qty=Decimal("0"),
        opening_inventory_amount=Decimal("0"),
        sales_amount=Decimal("999999"),  # 内訳合計 999,999 != 残高 800,000
        home_consumption=Decimal("0"),
        closing_inventory_qty=Decimal("0"),
        closing_inventory_amount=Decimal("0"),
    )
    with pytest.raises(ValueError, match="4310"):
        aggregation.assemble_agricultural_income(
            [bad],
            [],
            [],
            [],
            [],
            [],
            balances={"4310": Decimal("800000")},
            home_consumption_account="4350",
            inventory_account="1185",
            fiscal_year="FY2025",
            start_date=FY_START,
            end_date=FY_END,
        )

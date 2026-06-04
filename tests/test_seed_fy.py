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

from ai_books.models import AccountType, EntrySide
from ai_books.reports import (
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    worksheet_snapshot,
)
from tests.fixtures.seed_fy import (
    FY_ENTRIES,
    MONTHLY_TREND_ACCOUNTS,
    SeedEntry,
    SeedLine,
    diff_snapshots,
    general_ledger_from_dataset,
    journal_book_from_dataset,
    load_golden,
    monthly_trend_from_dataset,
    monthly_trend_snapshot,
    profit_and_loss_from_dataset,
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

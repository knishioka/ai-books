"""Property-based invariants over the 集計/決算書 layer (Issue #57).

Where ``test_seed_fy.py`` pins *one* hand-traced year against golden, this module asserts the
double-entry invariants hold for *every* generated year — random balanced 仕訳群 produced by
:mod:`tests.fixtures.seed_fy.generators`. These run with no database (offline reducers only), so they
are part of ``./scripts/verify.sh`` and exercise the same pure aggregation engines the DB path uses.

The invariants under test (each a property the books must satisfy regardless of the data):

* 借貸平均 — a balanced year's 試算表 has equal 借方/貸方 footings.
* 貸借一致 — 資産 = 負債 + 純資産 (当期純利益 込) on the 貸借対照表.
* articulation — 当期純利益 agrees between the 損益計算書 and the 貸借対照表 (and nothing is 未分類).
* 自己検算 — the 精算表 columns foot and its two 当期純利益 derivations agree.
* 連続性 — each account's 月次推移 reconciles 期首残高 + Σ期中増減 = 期末残高 and ties to its 試算表残高.
* ``Decimal`` 精度 — amounts round-trip through the model and aggregation with zero error.

Hypothesis is configured deterministically (``derandomize=True``) in :mod:`tests.conftest`, so a
counterexample is reproducible and a green run is stable across machines (AC: 決定的に再現可能).

The tail of the module pins the committed エッジケース golden (空 FY / 片側のみ / 端数多発 / 月跨ぎ整理) and
hand-checks the corner each isolates.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given

from ai_books.models import EntrySide, JournalLine
from ai_books.models.journal import validate_amount
from tests.fixtures.seed_fy import (
    EDGE_DATASETS,
    GOLDEN_REPORTS,
    SeedEntry,
    balance_sheet_from_dataset,
    diff_snapshots,
    load_golden,
    profit_and_loss_from_dataset,
    trial_balance_from_dataset,
    worksheet_from_dataset,
)
from tests.fixtures.seed_fy.dataset import account_type
from tests.fixtures.seed_fy.generators import amounts, balanced_datasets
from tests.fixtures.seed_fy.reports import monthly_trend_from_dataset

# ── 借貸平均 / 貸借一致 / articulation (any balanced year) ──────────────────────────


@given(balanced_datasets())
def test_trial_balance_always_balances(entries: tuple[SeedEntry, ...]) -> None:
    # Invariant: for *any* balanced 仕訳群, the 試算表 借方合計 = 貸方合計 (借貸平均) — always.
    trial_balance = trial_balance_from_dataset(entries)
    assert trial_balance.is_balanced
    assert trial_balance.total_debit == trial_balance.total_credit


@given(balanced_datasets())
def test_balance_sheet_identity_holds(entries: tuple[SeedEntry, ...]) -> None:
    # Invariant: 資産 = 負債 + 純資産 (当期純利益 込) — the 貸借対照表 closes for any balanced year.
    balance_sheet = balance_sheet_from_dataset(entries)
    assert balance_sheet.is_balanced
    assert (
        balance_sheet.total_assets == balance_sheet.total_liabilities + balance_sheet.total_equity
    )


@given(balanced_datasets())
def test_profit_and_loss_articulates_with_balance_sheet(entries: tuple[SeedEntry, ...]) -> None:
    # Invariant (articulation): 当期純利益 is the same figure on the PL and the BS, and every
    # 収益/費用 account is classified (網羅性) so the staged PL net equals Σ収益 - Σ費用.
    profit_and_loss = profit_and_loss_from_dataset(entries)
    balance_sheet = balance_sheet_from_dataset(entries)
    assert profit_and_loss.unclassified == []
    assert profit_and_loss.net_income == balance_sheet.net_income

    trial_balance = trial_balance_from_dataset(entries)
    revenue = sum(
        (row.balance for row in trial_balance.rows if account_type(row.code).value == "revenue"),
        Decimal(0),
    )
    expense = sum(
        (row.balance for row in trial_balance.rows if account_type(row.code).value == "expense"),
        Decimal(0),
    )
    assert profit_and_loss.net_income == revenue - expense


@given(balanced_datasets())
def test_worksheet_is_self_balancing(entries: tuple[SeedEntry, ...]) -> None:
    # Invariant (精算表の自己検算): every column foots and 当期純利益 agrees across 損益計算書欄/貸借対照表欄.
    worksheet = worksheet_from_dataset(entries)
    assert worksheet.is_trial_balanced
    assert worksheet.is_adjustments_balanced
    assert worksheet.pl_net_income == worksheet.bs_net_income
    assert worksheet.is_consistent


@given(balanced_datasets())
def test_monthly_trend_is_continuous(entries: tuple[SeedEntry, ...]) -> None:
    # Invariant (連続性): for every touched account, 期首残高 + Σ期中増減 = 期末残高, and that 期末残高
    # equals the account's 試算表残高 (one truth for the closing balance).
    trial_balance = {row.code: row.balance for row in trial_balance_from_dataset(entries).rows}
    touched = {line.account_code for entry in entries for line in entry.lines}
    for code in sorted(touched):
        trend = monthly_trend_from_dataset(code, entries)
        assert trend.is_consistent, f"{code}: 期首 + Σ期中増減 ≠ 期末"
        assert len(trend.points) == 12, f"{code}: FY2025 should tile into 12 months"
        assert trend.closing_balance == trial_balance[code], f"{code}: 期末残高 ≠ 試算表残高"


# ── Decimal 精度 (round-trips with zero error) ───────────────────────────────────


@given(amounts())
def test_amount_round_trips_through_the_model_with_zero_error(amount: Decimal) -> None:
    # AC: 金額 Decimal は往復で誤差ゼロ — a valid amount survives validation and a JSON round-trip
    # through the model exactly (no float detour, no rounding).
    assert validate_amount(amount) == amount
    line = JournalLine(account_id=1, side=EntrySide.DEBIT, amount=amount)
    assert line.amount == amount
    restored = JournalLine.model_validate_json(line.model_dump_json())
    assert restored.amount == amount
    assert isinstance(restored.amount, Decimal)


@given(balanced_datasets(min_size=1))
def test_aggregation_preserves_decimal_precision(entries: tuple[SeedEntry, ...]) -> None:
    # AC: 集計が Decimal 精度を保つ — the 試算表 footings equal an independent exact-Decimal sum of
    # the line amounts, with no precision lost in the reduction.
    expected_debit = sum(
        (line.amount for entry in entries for line in entry.lines if line.side is EntrySide.DEBIT),
        Decimal(0),
    )
    expected_credit = sum(
        (line.amount for entry in entries for line in entry.lines if line.side is EntrySide.CREDIT),
        Decimal(0),
    )
    trial_balance = trial_balance_from_dataset(entries)
    assert trial_balance.total_debit == expected_debit
    assert trial_balance.total_credit == expected_credit
    assert isinstance(trial_balance.total_debit, Decimal)


# ── エッジケース golden + hand-checks (Issue #57) ─────────────────────────────────

#: The ``<report>__<name>`` golden keys :mod:`tests.fixtures.seed_fy.golden` registered for the edge
#: years (the main reports have no ``__`` in their key), sorted for stable parametrization.
_EDGE_GOLDEN_KEYS: tuple[str, ...] = tuple(sorted(key for key in GOLDEN_REPORTS if "__" in key))


@pytest.mark.parametrize("report", _EDGE_GOLDEN_KEYS)
def test_edge_golden_files_are_up_to_date(report: str) -> None:
    # AC: 追加エッジケースで集計/PL/BS/帳簿が正しい (golden 化) — each committed edge golden matches the
    # offline reduction of its dataset, the same誤上書き防止 contract the main golden has.
    _filename, generate = GOLDEN_REPORTS[report]
    problems = diff_snapshots(load_golden(report), generate())
    assert problems == [], (
        f"golden/edge for {report} is stale; regenerate with "
        f"`python -m tests.fixtures.seed_fy --update`:\n  - " + "\n  - ".join(problems)
    )


def test_empty_year_aggregates_to_nothing() -> None:
    # 空 FY: no rows, zero footings, and a balanced (trivially) 貸借対照表.
    trial_balance = trial_balance_from_dataset(EDGE_DATASETS["empty"])
    assert trial_balance.rows == []
    assert trial_balance.total_debit == trial_balance.total_credit == Decimal(0)
    balance_sheet = balance_sheet_from_dataset(EDGE_DATASETS["empty"])
    assert balance_sheet.is_balanced
    assert balance_sheet.total_assets == Decimal(0)
    assert profit_and_loss_from_dataset(EDGE_DATASETS["empty"]).net_income == Decimal(0)


def test_one_sided_accounts_have_a_zero_column() -> None:
    # 片側のみ科目: every touched account shows a zero on the side it never posted to, and 普通預金
    # (an asset) ends with a 貸方残高 (正常残高と逆 / 負) — the rare case the BS must still place.
    rows = {row.code: row for row in trial_balance_from_dataset(EDGE_DATASETS["one_sided"]).rows}
    for row in rows.values():
        assert row.debit_total == Decimal(0) or row.credit_total == Decimal(0)
    assert rows["1141"].credit_total > 0
    assert rows["1141"].debit_total == Decimal(0)
    assert rows["1141"].balance == Decimal("-30000")  # 資産が貸方残高 (負)


def test_fractional_year_sums_without_drift() -> None:
    # 端数多発: Σ of the 1-sen amounts is exact (no float drift) — 0.01+0.02+0.03+0.33+0.67+1.10+99.00.
    trial_balance = trial_balance_from_dataset(EDGE_DATASETS["fractional"])
    assert trial_balance.total_debit == trial_balance.total_credit == Decimal("101.16")
    consumables = next(row for row in trial_balance.rows if row.code == "7200")
    assert consumables.debit_total == Decimal("101.16")


def test_cross_month_adjustments_route_by_source_not_date() -> None:
    # 月跨ぎ整理: 期末整理仕訳 booked in different months (6月/12月) land in 修正記入, not 残高試算表 —
    # the split keys off ``source``, and the 修正記入 columns still foot (借貸が揃う).
    rows = {
        row.code: row
        for row in worksheet_from_dataset(EDGE_DATASETS["cross_month_adjustment"]).rows
    }
    assert rows["7250"].trial_debit == Decimal("120000")  # operating entry → 残高試算表
    assert rows["7250"].adjustment_credit == Decimal("48000")  # 家事按分 (12月) → 修正記入
    assert rows["6330"].adjustment_debit == Decimal("60000")  # 減価償却 (6月) → 修正記入
    worksheet = worksheet_from_dataset(EDGE_DATASETS["cross_month_adjustment"])
    assert worksheet.is_adjustments_balanced
    assert worksheet.is_consistent

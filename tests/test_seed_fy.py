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

from ai_books.models import EntrySide
from ai_books.reports import general_ledger_snapshot, journal_book_snapshot
from tests.fixtures.seed_fy import (
    FY_ENTRIES,
    SeedEntry,
    SeedLine,
    diff_snapshots,
    general_ledger_from_dataset,
    journal_book_from_dataset,
    load_golden,
    trial_balance_from_dataset,
    trial_balance_snapshot,
    validate_dataset,
)
from tests.fixtures.seed_fy import golden as golden_mod


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

"""Pure (no-DB) tests for the 帳簿レポート output formatters (Issue #19).

Exercise the JSON snapshot / CSV / 整形テキスト renderers over both the synthetic dataset
and small hand-built models. These run everywhere (no Postgres) — the DB round-trip that
checks the *data* against golden lives in ``test_seed_fy_db.py``; here we pin the *output
shapes*, including that a 取消 (voided) 伝票 stays visible as history (電子帳簿保存).
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from ai_books.models import (
    BalanceSheet,
    BalanceSheetLine,
    BalanceSheetSection,
    EntrySide,
    EntryStatus,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
    ProfitAndLoss,
    ProfitAndLossLine,
    ProfitAndLossSection,
    StatementCategory,
)
from ai_books.reports import (
    balance_sheet_snapshot,
    balance_sheet_text,
    general_ledger_csv,
    general_ledger_snapshot,
    general_ledger_text,
    journal_book_csv,
    journal_book_snapshot,
    journal_book_text,
    money,
    profit_and_loss_snapshot,
    profit_and_loss_text,
)
from tests.fixtures.seed_fy import (
    balance_sheet_from_dataset,
    general_ledger_from_dataset,
    journal_book_from_dataset,
    profit_and_loss_from_dataset,
)


def test_money_is_fixed_two_decimals() -> None:
    assert money(Decimal("300000")) == "300000.00"
    assert money(Decimal("-350000")) == "-350000.00"
    assert money(Decimal("0")) == "0.00"


def test_journal_book_snapshot_is_jsonable_and_ordered() -> None:
    snapshot = journal_book_snapshot(journal_book_from_dataset())
    assert snapshot["report"] == "journal_book"
    assert snapshot["status"] == "posted"
    assert snapshot["total_debit"] == snapshot["total_credit"] == "10791500.00"
    # Amounts are strings (浮動小数禁止), entries kept in book order.
    first = snapshot["entries"][0]
    assert first["voucher_no"] == "FY2025-000"
    assert isinstance(first["lines"][0]["amount"], str)


def test_general_ledger_snapshot_carries_running_balance_and_counters() -> None:
    snapshot = general_ledger_snapshot(general_ledger_from_dataset())
    assert snapshot["report"] == "general_ledger"
    cash = next(a for a in snapshot["accounts"] if a["code"] == "1110")
    assert cash["opening_balance"] == "0.00"
    assert cash["closing_balance"] == "300000.00"
    assert [r["running_balance"] for r in cash["rows"]] == [
        "200000.00",
        "420000.00",
        "340000.00",
        "300000.00",
    ]
    # 諸口: the opening entry lists several 相手科目 on the cash row.
    assert cash["rows"][0]["counter_accounts"] == ["1141", "1180", "1530", "2510", "3110"]


def test_journal_book_csv_has_one_row_per_line() -> None:
    book = journal_book_from_dataset()
    text = journal_book_csv(book)
    rows = list(csv.reader(io.StringIO(text)))
    expected_lines = sum(len(e.lines) for e in book.entries)
    assert rows[0] == [
        "voucher_no",
        "entry_date",
        "status",
        "description",
        "account_code",
        "account_name",
        "side",
        "amount",
        "line_description",
    ]
    assert len(rows) == expected_lines + 1  # + header


def test_general_ledger_csv_brackets_each_account_with_carry_rows() -> None:
    ledger = general_ledger_from_dataset()
    rows = list(csv.reader(io.StringIO(general_ledger_csv(ledger))))
    notes = [r[-1] for r in rows[1:]]
    # Every account contributes exactly one 繰越 and one 期末残高 marker row.
    assert notes.count("繰越") == len(ledger.accounts)
    assert notes.count("期末残高") == len(ledger.accounts)


def _voided_book() -> JournalBook:
    """A one-entry 仕訳帳 whose single 伝票 has been 取消 (voided) with a reason."""
    entry = JournalBookEntry(
        entry_date=date(2025, 2, 15),
        voucher_no="V0000001",
        description="売上 (現金) — 重複",
        status=EntryStatus.VOIDED,
        void_reason="重複計上のため取消",
        lines=[
            JournalBookLine(
                account_code="1110",
                account_name="現金",
                side=EntrySide.DEBIT,
                amount=Decimal("220000"),
            ),
            JournalBookLine(
                account_code="4110",
                account_name="売上高",
                side=EntrySide.CREDIT,
                amount=Decimal("220000"),
            ),
        ],
    )
    return JournalBook(
        start_date=None,
        end_date=None,
        status=EntryStatus.VOIDED,
        entries=[entry],
        total_debit=Decimal("220000"),
        total_credit=Decimal("220000"),
    )


def test_voided_entry_stays_visible_as_history() -> None:
    # AC (#19): 取消/訂正仕訳が履歴として追える — a 取消 伝票 keeps its reason in the book.
    book = _voided_book()
    snapshot = journal_book_snapshot(book)
    entry = snapshot["entries"][0]
    assert entry["status"] == "voided"
    assert entry["void_reason"] == "重複計上のため取消"

    text = journal_book_text(book)
    assert "[取消: 重複計上のため取消]" in text


def test_general_ledger_text_renders_carry_and_balance() -> None:
    text = general_ledger_text(general_ledger_from_dataset())
    assert "総勘定元帳" in text
    assert "繰越" in text
    assert "期末残高" in text


# --- 貸借対照表 (Issue #21) ------------------------------------------------------


def test_balance_sheet_snapshot_is_jsonable_and_balanced() -> None:
    snapshot = balance_sheet_snapshot(balance_sheet_from_dataset())
    assert snapshot["report"] == "balance_sheet"
    # Amounts are fixed-point strings (浮動小数禁止), 当期純利益 is a loss this year.
    assert snapshot["total_assets"] == "3319500.00"
    assert snapshot["net_income"] == "-580500.00"
    assert snapshot["total_equity"] == "2719500.00"
    # 貸借一致 visible in the file: 資産合計 = 負債合計 + 純資産合計.
    liabilities_equity = Decimal(snapshot["total_liabilities"]) + Decimal(snapshot["total_equity"])
    assert Decimal(snapshot["total_assets"]) == liabilities_equity
    # Sections kept in statement order; a line names its 勘定科目 inline.
    assert [s["category"] for s in snapshot["assets"]] == ["current_assets", "fixed_assets"]
    cash = next(line for line in snapshot["assets"][0]["lines"] if line["code"] == "1110")
    assert cash["name"] == "現金"
    assert isinstance(cash["balance"], str)


def test_balance_sheet_text_renders_sides_and_net_income() -> None:
    text = balance_sheet_text(balance_sheet_from_dataset())
    assert "貸借対照表" in text
    assert "資産合計" in text
    assert "負債合計" in text
    assert "当期純利益  -580500.00" in text
    assert "純資産合計  2719500.00" in text
    # 負債・純資産合計 closes against 資産合計.
    assert "負債・純資産合計  3319500.00" in text


def _unbalanced_balance_sheet() -> BalanceSheet:
    """A hand-built B/S whose sides do not foot — so :attr:`is_balanced` can be exercised."""
    return BalanceSheet(
        assets=[
            BalanceSheetSection(
                category=StatementCategory.CURRENT_ASSETS,
                lines=[BalanceSheetLine(code="1110", name="現金", balance=Decimal("100"))],
                subtotal=Decimal("100"),
            )
        ],
        liabilities=[],
        equity=[],
        net_income=Decimal("0"),
        total_assets=Decimal("100"),
        total_liabilities=Decimal("0"),
        total_equity=Decimal("0"),
    )


def test_balance_sheet_is_balanced_flag() -> None:
    assert not _unbalanced_balance_sheet().is_balanced
    assert balance_sheet_from_dataset().is_balanced


# --- 損益計算書 (profit & loss, Issue #20) --------------------------------------


def test_profit_and_loss_snapshot_is_jsonable_and_staged() -> None:
    snapshot = profit_and_loss_snapshot(profit_and_loss_from_dataset())
    assert snapshot["report"] == "profit_and_loss"
    assert snapshot["fiscal_year"] == "FY2025"
    assert snapshot["start_date"] == "2025-01-01"
    assert snapshot["end_date"] == "2025-12-31"
    # Sections carry a subtotal and 科目別 lines; amounts are strings (浮動小数禁止).
    assert snapshot["sales"]["subtotal"] == "1650000.00"
    assert isinstance(snapshot["sales"]["lines"][0]["amount"], str)
    assert snapshot["cost_of_goods_sold"]["subtotal"] == "1490000.00"  # 製造原価を含む
    # The derived 段階利益 sit between the sections.
    assert snapshot["gross_profit"] == "160000.00"
    assert snapshot["operating_income"] == "-560000.00"
    assert snapshot["ordinary_income"] == "-580500.00"
    assert snapshot["net_income"] == "-580500.00"
    assert snapshot["unclassified"] == []


def test_profit_and_loss_text_renders_each_stage() -> None:
    text = profit_and_loss_text(profit_and_loss_from_dataset())
    assert "損益計算書" in text
    for stage in ("売上総利益", "営業利益", "経常利益", "当期純利益"):
        assert stage in text
    assert "-580500.00" in text  # 当期純損失


def _unclassified_pl() -> ProfitAndLoss:
    """A P/L carrying one 未分類 expense (表示区分なし) to pin the surfacing behaviour."""

    def empty(key: str) -> ProfitAndLossSection:
        # A fresh instance per field — DomainModel allows assignment, so sharing one
        # mutable section across fields could alias edits between them.
        return ProfitAndLossSection(key=key, label=key, lines=[], subtotal=Decimal("0"))

    return ProfitAndLoss(
        fiscal_year="FY2025",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        sales=ProfitAndLossSection(
            key="sales",
            label="売上高",
            lines=[
                ProfitAndLossLine(
                    code="4110",
                    name="売上高",
                    category=StatementCategory.SALES,
                    amount=Decimal("1000"),
                )
            ],
            subtotal=Decimal("1000"),
        ),
        cost_of_goods_sold=empty("cost_of_goods_sold"),
        gross_profit=Decimal("1000"),
        selling_admin_expenses=empty("selling_admin_expenses"),
        operating_income=Decimal("1000"),
        non_operating_income=empty("non_operating_income"),
        non_operating_expenses=empty("non_operating_expenses"),
        ordinary_income=Decimal("1000"),
        net_income=Decimal("1000"),
        unclassified=[
            ProfitAndLossLine(code="9999", name="謎の費用", category=None, amount=Decimal("500"))
        ],
    )


def test_profit_and_loss_surfaces_unclassified_accounts() -> None:
    # AC (#20): 未分類科目を検出 — an un-categorised account shows up in both outputs.
    pl = _unclassified_pl()
    snapshot = profit_and_loss_snapshot(pl)
    assert snapshot["unclassified"] == [
        {"code": "9999", "name": "謎の費用", "category": None, "amount": "500.00"}
    ]
    text = profit_and_loss_text(pl)
    assert "未分類科目" in text
    assert "9999 謎の費用" in text

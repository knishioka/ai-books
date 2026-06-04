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
    EntrySide,
    EntryStatus,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
)
from ai_books.reports import (
    general_ledger_csv,
    general_ledger_snapshot,
    general_ledger_text,
    journal_book_csv,
    journal_book_snapshot,
    journal_book_text,
    money,
    worksheet_csv,
    worksheet_snapshot,
    worksheet_text,
)
from tests.fixtures.seed_fy import (
    general_ledger_from_dataset,
    journal_book_from_dataset,
    worksheet_from_dataset,
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


# --- 精算表 (worksheet, Issue #22) ---------------------------------------------


def test_worksheet_snapshot_carries_columns_and_net_income() -> None:
    snapshot = worksheet_snapshot(worksheet_from_dataset())
    assert snapshot["report"] == "worksheet"
    assert snapshot["fiscal_year"] == "FY2025"
    # The footings let a reader re-check 借貸平均 and the 自己検算 from the file alone.
    assert snapshot["trial_debit_total"] == snapshot["trial_credit_total"]
    assert snapshot["adjustment_debit_total"] == snapshot["adjustment_credit_total"]
    assert snapshot["net_income"] == "-580500.00"
    rent = next(row for row in snapshot["rows"] if row["code"] == "7250")
    assert rent["adjustment_credit"] == "240000.00"  # 家事按分 in 修正記入
    assert rent["pl_debit"] == "360000.00"  # adjusted into the 損益計算書欄


def test_worksheet_csv_has_totals_and_net_income_rows() -> None:
    rows = list(csv.reader(io.StringIO(worksheet_csv(worksheet_from_dataset()))))
    header, *body = rows
    assert header[:3] == ["code", "name", "account_type"]
    labels = [row[1] for row in body]
    assert "合計" in labels
    assert "当期純利益" in labels
    net_income_row = next(row for row in body if row[1] == "当期純利益")
    # 当期純利益 sits in the 損益計算書欄 借方 and the 貸借対照表欄 貸方 columns.
    assert net_income_row[7] == "-580500.00"  # pl_debit column
    assert net_income_row[10] == "-580500.00"  # bs_credit column


def test_worksheet_text_renders_panels_and_net_income() -> None:
    text = worksheet_text(worksheet_from_dataset())
    assert "精算表" in text
    assert "当期純利益 -580500.00" in text

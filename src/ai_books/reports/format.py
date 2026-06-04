"""Serialize 仕訳帳 / 総勘定元帳 report data to JSON snapshot, CSV, and 整形テキスト.

Every amount is rendered through :func:`money` as a fixed 2-dp string ("300000.00",
never "300000" or "3E+5"), matching ``numeric(18, 2)`` so output is byte-stable
regardless of how the underlying :class:`~decimal.Decimal` was built — this is what makes
the JSON snapshots safe to freeze as golden files. The snapshot dicts are JSON-ready
(only ``str`` / ``None`` / ``list`` / ``dict``) and are the structure the Vercel viewer
(#25) renders from.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

from ai_books.models import (
    BalanceSheet,
    BalanceSheetSection,
    EntrySide,
    EntryStatus,
    GeneralLedger,
    GeneralLedgerAccount,
    JournalBook,
    JournalBookEntry,
    StatementCategory,
)

#: Two-decimal quantum matching ``numeric(18, 2)``.
_MONEY = Decimal("0.01")


def money(value: Decimal) -> str:
    """Serialize a Decimal amount as a fixed 2-dp string (numeric(18, 2) shape)."""
    return str(value.quantize(_MONEY))


def _iso(value: date | None) -> str | None:
    """ISO-format a date, passing ``None`` through (for an open-ended window bound)."""
    return None if value is None else value.isoformat()


def _status(value: EntryStatus | None) -> str | None:
    """The stored string for an entry status, or ``None`` when no filter was applied."""
    return None if value is None else value.value


# --- 仕訳帳 (journal book) ------------------------------------------------------


def journal_book_snapshot(book: JournalBook) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.JournalBook` into its canonical JSON shape.

    Entries stay in 取引日 → 伝票番号 order; each line names its 勘定科目 inline so the book
    is self-contained. The column footings are included so 借貸平均 is visible in the file.
    """
    return {
        "report": "journal_book",
        "start_date": _iso(book.start_date),
        "end_date": _iso(book.end_date),
        "status": _status(book.status),
        "entries": [_journal_book_entry(entry) for entry in book.entries],
        "total_debit": money(book.total_debit),
        "total_credit": money(book.total_credit),
    }


def _journal_book_entry(entry: JournalBookEntry) -> dict[str, Any]:
    return {
        "voucher_no": entry.voucher_no,
        "entry_date": entry.entry_date.isoformat(),
        "description": entry.description,
        "status": entry.status.value,
        "void_reason": entry.void_reason,
        "lines": [
            {
                "account_code": line.account_code,
                "account_name": line.account_name,
                "side": line.side.value,
                "amount": money(line.amount),
                "line_description": line.line_description,
            }
            for line in entry.lines
        ],
    }


_JOURNAL_BOOK_CSV_HEADER = [
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


def journal_book_csv(book: JournalBook) -> str:
    """Render the 仕訳帳 as CSV — one row per 明細, entries kept in book order."""
    # newline="" so csv.writer controls line endings (avoids \r\r\n on Windows — see csv docs).
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_JOURNAL_BOOK_CSV_HEADER)
    for entry in book.entries:
        for line in entry.lines:
            writer.writerow(
                [
                    entry.voucher_no or "",
                    entry.entry_date.isoformat(),
                    entry.status.value,
                    entry.description or "",
                    line.account_code,
                    line.account_name,
                    line.side.value,
                    money(line.amount),
                    line.line_description or "",
                ]
            )
    return buffer.getvalue()


def journal_book_text(book: JournalBook) -> str:
    """Render the 仕訳帳 as 整形テキスト for human inspection."""
    lines: list[str] = [
        "仕訳帳 (Journal Book)",
        f"  期間: {_period_label(book.start_date, book.end_date)}",
    ]
    for entry in book.entries:
        header = (
            f"{entry.entry_date.isoformat()}  {entry.voucher_no or '-'}  {entry.description or ''}"
        )
        if entry.status is EntryStatus.VOIDED:
            header += f"  [取消: {entry.void_reason or ''}]"
        lines.append(header)
        for line in entry.lines:
            side = "借" if line.side is EntrySide.DEBIT else "貸"
            lines.append(
                f"    {side}  {line.account_code} {line.account_name}  {money(line.amount)}"
            )
    lines.append(f"  借方合計 {money(book.total_debit)} / 貸方合計 {money(book.total_credit)}")
    return "\n".join(lines) + "\n"


# --- 総勘定元帳 (general ledger) ------------------------------------------------


def general_ledger_snapshot(ledger: GeneralLedger) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.GeneralLedger` into its canonical JSON shape.

    Accounts stay in 科目コード順, each with its 繰越 (opening) and 期末残高 (closing) and the
    per-row running balance. Every row carries its 伝票番号 so a figure is traceable.
    """
    return {
        "report": "general_ledger",
        "start_date": _iso(ledger.start_date),
        "end_date": _iso(ledger.end_date),
        "status": _status(ledger.status),
        "accounts": [_general_ledger_account(account) for account in ledger.accounts],
    }


def _general_ledger_account(account: GeneralLedgerAccount) -> dict[str, Any]:
    return {
        "code": account.code,
        "name": account.name,
        "normal_balance": account.normal_balance.value,
        "opening_balance": money(account.opening_balance),
        "closing_balance": money(account.closing_balance),
        "rows": [
            {
                "entry_date": row.entry_date.isoformat(),
                "voucher_no": row.voucher_no,
                "description": row.description,
                "line_description": row.line_description,
                "counter_accounts": list(row.counter_accounts),
                "side": row.side.value,
                "amount": money(row.amount),
                "running_balance": money(row.running_balance),
            }
            for row in account.rows
        ],
    }


_GENERAL_LEDGER_CSV_HEADER = [
    "account_code",
    "account_name",
    "entry_date",
    "voucher_no",
    "counter_accounts",
    "side",
    "amount",
    "running_balance",
    "note",
]


def general_ledger_csv(ledger: GeneralLedger) -> str:
    """Render the 総勘定元帳 as CSV — a 繰越 row, the detail rows, then a 期末残高 row per account."""
    # newline="" so csv.writer controls line endings (avoids \r\r\n on Windows — see csv docs).
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_GENERAL_LEDGER_CSV_HEADER)
    for account in ledger.accounts:
        writer.writerow(
            [account.code, account.name, "", "", "", "", "", money(account.opening_balance), "繰越"]
        )
        for row in account.rows:
            writer.writerow(
                [
                    account.code,
                    account.name,
                    row.entry_date.isoformat(),
                    row.voucher_no or "",
                    ";".join(row.counter_accounts),
                    row.side.value,
                    money(row.amount),
                    money(row.running_balance),
                    "",
                ]
            )
        writer.writerow(
            [
                account.code,
                account.name,
                "",
                "",
                "",
                "",
                "",
                money(account.closing_balance),
                "期末残高",
            ]
        )
    return buffer.getvalue()


def general_ledger_text(ledger: GeneralLedger) -> str:
    """Render the 総勘定元帳 as 整形テキスト for human inspection."""
    lines: list[str] = [
        "総勘定元帳 (General Ledger)",
        f"  期間: {_period_label(ledger.start_date, ledger.end_date)}",
    ]
    for account in ledger.accounts:
        lines.append(f"[{account.code}] {account.name}  繰越 {money(account.opening_balance)}")
        for row in account.rows:
            side = "借" if row.side is EntrySide.DEBIT else "貸"
            counter = "/".join(row.counter_accounts) or "-"
            lines.append(
                f"    {row.entry_date.isoformat()}  {row.voucher_no or '-'}  {side} "
                f"{money(row.amount)}  相手 {counter}  残 {money(row.running_balance)}"
            )
        lines.append(f"    期末残高 {money(account.closing_balance)}")
    return "\n".join(lines) + "\n"


# --- 貸借対照表 (balance sheet, Issue #21) -------------------------------------

#: 表示区分 → 日本語見出し for the 整形テキスト rendering.
_CATEGORY_LABELS: dict[StatementCategory, str] = {
    StatementCategory.CURRENT_ASSETS: "流動資産",
    StatementCategory.FIXED_ASSETS: "固定資産",
    StatementCategory.CURRENT_LIABILITIES: "流動負債",
    StatementCategory.FIXED_LIABILITIES: "固定負債",
    StatementCategory.EQUITY: "純資産",
}


def _balance_sheet_section(section: BalanceSheetSection) -> dict[str, Any]:
    return {
        "category": section.category.value,
        "lines": [
            {"code": line.code, "name": line.name, "balance": money(line.balance)}
            for line in section.lines
        ],
        "subtotal": money(section.subtotal),
    }


def balance_sheet_snapshot(balance_sheet: BalanceSheet) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.BalanceSheet` into its canonical JSON shape.

    The three sides keep their sections in statement order (流動→固定 for 資産・負債), each line
    naming its 勘定科目 inline. ``net_income`` (当期純利益) and the three totals are included so
    貸借一致 (``total_assets`` == ``total_liabilities`` + ``total_equity``) is visible in the file.
    """
    return {
        "report": "balance_sheet",
        "as_of": _iso(balance_sheet.as_of),
        "status": _status(balance_sheet.status),
        "assets": [_balance_sheet_section(section) for section in balance_sheet.assets],
        "liabilities": [_balance_sheet_section(section) for section in balance_sheet.liabilities],
        "equity": [_balance_sheet_section(section) for section in balance_sheet.equity],
        "net_income": money(balance_sheet.net_income),
        "total_assets": money(balance_sheet.total_assets),
        "total_liabilities": money(balance_sheet.total_liabilities),
        "total_equity": money(balance_sheet.total_equity),
    }


def _balance_sheet_section_text(section: BalanceSheetSection, lines: list[str]) -> None:
    """Append one 表示区分 block (見出し → 明細 → 区分小計) to ``lines``."""
    label = _CATEGORY_LABELS.get(section.category, section.category.value)
    lines.append(f"  {label}")
    for line in section.lines:
        lines.append(f"    {line.code} {line.name}  {money(line.balance)}")
    lines.append(f"    {label} 計  {money(section.subtotal)}")


def balance_sheet_text(balance_sheet: BalanceSheet) -> str:
    """Render the 貸借対照表 as 整形テキスト for human inspection.

    Lays out 資産の部 / 負債の部 / 純資産の部 with per-区分 subtotals, shows 当期純利益 as the last
    純資産 figure, and closes with 資産合計 against 負債・純資産合計 so 貸借一致 is eyeballable.
    """
    as_of = balance_sheet.as_of.isoformat() if balance_sheet.as_of is not None else "全期間"
    lines: list[str] = ["貸借対照表 (Balance Sheet)", f"  時点: {as_of}", "【資産の部】"]
    for section in balance_sheet.assets:
        _balance_sheet_section_text(section, lines)
    lines.append(f"  資産合計  {money(balance_sheet.total_assets)}")
    lines.append("【負債の部】")
    for section in balance_sheet.liabilities:
        _balance_sheet_section_text(section, lines)
    lines.append(f"  負債合計  {money(balance_sheet.total_liabilities)}")
    lines.append("【純資産の部】")
    for section in balance_sheet.equity:
        _balance_sheet_section_text(section, lines)
    lines.append(f"  当期純利益  {money(balance_sheet.net_income)}")
    lines.append(f"  純資産合計  {money(balance_sheet.total_equity)}")
    liabilities_equity = balance_sheet.total_liabilities + balance_sheet.total_equity
    lines.append(f"  負債・純資産合計  {money(liabilities_equity)}")
    return "\n".join(lines) + "\n"


def _period_label(start: date | None, end: date | None) -> str:
    """Human label for an optional date window ("全期間" when both bounds are open)."""
    if start is None and end is None:
        return "全期間"
    return f"{_iso(start) or '開始'} 〜 {_iso(end) or '終了'}"

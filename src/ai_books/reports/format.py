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
    EntryStatus,
    GeneralLedger,
    GeneralLedgerAccount,
    JournalBook,
    JournalBookEntry,
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
    buffer = io.StringIO()
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
            side = "借" if line.side.value == "debit" else "貸"
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
    buffer = io.StringIO()
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
            side = "借" if row.side.value == "debit" else "貸"
            counter = "/".join(row.counter_accounts) or "-"
            lines.append(
                f"    {row.entry_date.isoformat()}  {row.voucher_no or '-'}  {side} "
                f"{money(row.amount)}  相手 {counter}  残 {money(row.running_balance)}"
            )
        lines.append(f"    期末残高 {money(account.closing_balance)}")
    return "\n".join(lines) + "\n"


def _period_label(start: date | None, end: date | None) -> str:
    """Human label for an optional date window ("全期間" when both bounds are open)."""
    if start is None and end is None:
        return "全期間"
    return f"{_iso(start) or '開始'} 〜 {_iso(end) or '終了'}"

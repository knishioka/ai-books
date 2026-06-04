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
    EntrySide,
    EntryStatus,
    GeneralLedger,
    GeneralLedgerAccount,
    JournalBook,
    JournalBookEntry,
    ProfitAndLoss,
    ProfitAndLossLine,
    ProfitAndLossSection,
    Worksheet,
    WorksheetRow,
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


def _period_label(start: date | None, end: date | None) -> str:
    """Human label for an optional date window ("全期間" when both bounds are open)."""
    if start is None and end is None:
        return "全期間"
    return f"{_iso(start) or '開始'} 〜 {_iso(end) or '終了'}"


# --- 精算表 (worksheet) ---------------------------------------------------------


def worksheet_snapshot(worksheet: Worksheet) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.Worksheet` into its canonical JSON shape.

    Rows stay in 科目コード順, each carrying its four column-pairs (残高試算表 / 修正記入 /
    損益計算書欄 / 貸借対照表欄). The eight column footings and 当期純利益 are included so the
    worksheet's 自己検算 (PL 欄と BS 欄の当期純利益が一致) is visible in the file itself.
    """
    return {
        "report": "worksheet",
        "fiscal_year": worksheet.fiscal_year,
        "rows": [_worksheet_row(row) for row in worksheet.rows],
        "trial_debit_total": money(worksheet.trial_debit_total),
        "trial_credit_total": money(worksheet.trial_credit_total),
        "adjustment_debit_total": money(worksheet.adjustment_debit_total),
        "adjustment_credit_total": money(worksheet.adjustment_credit_total),
        "pl_debit_total": money(worksheet.pl_debit_total),
        "pl_credit_total": money(worksheet.pl_credit_total),
        "bs_debit_total": money(worksheet.bs_debit_total),
        "bs_credit_total": money(worksheet.bs_credit_total),
        "net_income": money(worksheet.net_income),
    }


def _worksheet_row(row: WorksheetRow) -> dict[str, Any]:
    return {
        "code": row.code,
        "name": row.name,
        "account_type": row.account_type.value,
        "trial_debit": money(row.trial_debit),
        "trial_credit": money(row.trial_credit),
        "adjustment_debit": money(row.adjustment_debit),
        "adjustment_credit": money(row.adjustment_credit),
        "pl_debit": money(row.pl_debit),
        "pl_credit": money(row.pl_credit),
        "bs_debit": money(row.bs_debit),
        "bs_credit": money(row.bs_credit),
    }


_WORKSHEET_CSV_HEADER = [
    "code",
    "name",
    "account_type",
    "trial_debit",
    "trial_credit",
    "adjustment_debit",
    "adjustment_credit",
    "pl_debit",
    "pl_credit",
    "bs_debit",
    "bs_credit",
]


def worksheet_csv(worksheet: Worksheet) -> str:
    """Render the 精算表 as CSV — one row per 勘定科目, then a 合計 row and a 当期純利益 row.

    The 当期純利益 row carries the balancing figure on the side each column-pair needs it
    (損益計算書欄 借方 / 貸借対照表欄 貸方), so the footings below it foot column by column.
    """
    # newline="" so csv.writer controls line endings (avoids \r\r\n on Windows — see csv docs).
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_WORKSHEET_CSV_HEADER)
    for row in worksheet.rows:
        writer.writerow(
            [
                row.code,
                row.name,
                row.account_type.value,
                money(row.trial_debit),
                money(row.trial_credit),
                money(row.adjustment_debit),
                money(row.adjustment_credit),
                money(row.pl_debit),
                money(row.pl_credit),
                money(row.bs_debit),
                money(row.bs_credit),
            ]
        )
    writer.writerow(
        [
            "",
            "合計",
            "",
            money(worksheet.trial_debit_total),
            money(worksheet.trial_credit_total),
            money(worksheet.adjustment_debit_total),
            money(worksheet.adjustment_credit_total),
            money(worksheet.pl_debit_total),
            money(worksheet.pl_credit_total),
            money(worksheet.bs_debit_total),
            money(worksheet.bs_credit_total),
        ]
    )
    writer.writerow(
        [
            "",
            "当期純利益",
            "",
            "",
            "",
            "",
            "",
            money(worksheet.net_income),
            "",
            "",
            money(worksheet.net_income),
        ]
    )
    return buffer.getvalue()


def worksheet_text(worksheet: Worksheet) -> str:
    """Render the 精算表 as 整形テキスト for human inspection."""
    lines: list[str] = [
        "精算表 (Worksheet)",
        f"  会計年度: {worksheet.fiscal_year}  "
        f"期間: {_period_label(worksheet.start_date, worksheet.end_date)}",
        "  科目: [試算表 借/貸] [修正記入 借/貸] [PL 借/貸] [BS 借/貸]",
    ]
    for row in worksheet.rows:
        lines.append(
            f"  [{row.code}] {row.name}"
            f"  試 {money(row.trial_debit)}/{money(row.trial_credit)}"
            f"  修 {money(row.adjustment_debit)}/{money(row.adjustment_credit)}"
            f"  損 {money(row.pl_debit)}/{money(row.pl_credit)}"
            f"  貸 {money(row.bs_debit)}/{money(row.bs_credit)}"
        )
    lines.append(
        "  合計"
        f"  試 {money(worksheet.trial_debit_total)}/{money(worksheet.trial_credit_total)}"
        f"  修 {money(worksheet.adjustment_debit_total)}/{money(worksheet.adjustment_credit_total)}"
        f"  損 {money(worksheet.pl_debit_total)}/{money(worksheet.pl_credit_total)}"
        f"  貸 {money(worksheet.bs_debit_total)}/{money(worksheet.bs_credit_total)}"
    )
    lines.append(f"  当期純利益 {money(worksheet.net_income)}")
    return "\n".join(lines) + "\n"


# --- 損益計算書 (profit & loss, Issue #20) --------------------------------------


def _pl_line(line: ProfitAndLossLine) -> dict[str, Any]:
    return {
        "code": line.code,
        "name": line.name,
        "category": None if line.category is None else line.category.value,
        "amount": money(line.amount),
    }


def _pl_section(section: ProfitAndLossSection) -> dict[str, Any]:
    return {
        "key": section.key,
        "label": section.label,
        "lines": [_pl_line(line) for line in section.lines],
        "subtotal": money(section.subtotal),
    }


def profit_and_loss_snapshot(pl: ProfitAndLoss) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.ProfitAndLoss` into its canonical JSON shape.

    Sections stay in 段階表示 order, each with its 科目別 lines (科目コード順) and subtotal; the
    derived 段階利益 (売上総利益 / 営業利益 / 経常利益 / 当期純利益) sit between them, and any
    未分類科目 are listed so a coverage gap is visible in the file. Amounts are fixed-point
    strings (浮動小数禁止). This is the shape the golden harness freezes and the Vercel viewer
    (#25) renders.
    """
    return {
        "report": "profit_and_loss",
        "fiscal_year": pl.fiscal_year,
        "start_date": pl.start_date.isoformat(),
        "end_date": pl.end_date.isoformat(),
        "sales": _pl_section(pl.sales),
        "cost_of_goods_sold": _pl_section(pl.cost_of_goods_sold),
        "gross_profit": money(pl.gross_profit),
        "selling_admin_expenses": _pl_section(pl.selling_admin_expenses),
        "operating_income": money(pl.operating_income),
        "non_operating_income": _pl_section(pl.non_operating_income),
        "non_operating_expenses": _pl_section(pl.non_operating_expenses),
        "ordinary_income": money(pl.ordinary_income),
        "net_income": money(pl.net_income),
        "unclassified": [_pl_line(line) for line in pl.unclassified],
    }


def profit_and_loss_text(pl: ProfitAndLoss) -> str:
    """Render the 損益計算書 as 整形テキスト for human inspection (段階利益を挟んで表示)."""
    lines: list[str] = [
        "損益計算書 (Profit & Loss)",
        f"  会計年度: {pl.fiscal_year} ({pl.start_date.isoformat()} 〜 {pl.end_date.isoformat()})",
    ]

    def emit_section(section: ProfitAndLossSection) -> None:
        lines.append(f"【{section.label}】")
        for line in section.lines:
            lines.append(f"    {line.code} {line.name}  {money(line.amount)}")
        lines.append(f"    {section.label} 計  {money(section.subtotal)}")

    def emit_profit(label: str, amount: Decimal) -> None:
        lines.append(f"  {label}  {money(amount)}")

    emit_section(pl.sales)
    emit_section(pl.cost_of_goods_sold)
    emit_profit("売上総利益", pl.gross_profit)
    emit_section(pl.selling_admin_expenses)
    emit_profit("営業利益", pl.operating_income)
    emit_section(pl.non_operating_income)
    emit_section(pl.non_operating_expenses)
    emit_profit("経常利益", pl.ordinary_income)
    emit_profit("当期純利益", pl.net_income)

    if pl.unclassified:
        lines.append("【未分類科目 (表示区分なし)】")
        for line in pl.unclassified:
            lines.append(f"    {line.code} {line.name}  {money(line.amount)}")

    return "\n".join(lines) + "\n"

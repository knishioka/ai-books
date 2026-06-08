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
    AgriculturalIncome,
    BalanceSheet,
    BalanceSheetSection,
    DepreciationSchedule,
    EntrySide,
    EntryStatus,
    FinancialStatements,
    GeneralLedger,
    GeneralLedgerAccount,
    JournalBook,
    JournalBookEntry,
    ManufacturingCost,
    ManufacturingCostSection,
    MonthlySalesPurchases,
    ProfitAndLoss,
    ProfitAndLossLine,
    ProfitAndLossSection,
    RealEstateIncome,
    StatementCategory,
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


# --- 青色申告決算書 (financial statements, Issue #23) ---------------------------


def _manufacturing_section(section: ManufacturingCostSection) -> dict[str, Any]:
    return {
        "key": section.key,
        "label": section.label,
        "lines": [
            {"code": line.code, "name": line.name, "amount": money(line.amount)}
            for line in section.lines
        ],
        "subtotal": money(section.subtotal),
    }


def _manufacturing_cost_snapshot(manufacturing_cost: ManufacturingCost) -> dict[str, Any]:
    return {
        "materials": _manufacturing_section(manufacturing_cost.materials),
        "labor": _manufacturing_section(manufacturing_cost.labor),
        "overhead": _manufacturing_section(manufacturing_cost.overhead),
        "total_manufacturing_cost": money(manufacturing_cost.total_manufacturing_cost),
        "cost_of_goods_manufactured": money(manufacturing_cost.cost_of_goods_manufactured),
    }


def _monthly_snapshot(monthly: MonthlySalesPurchases) -> dict[str, Any]:
    return {
        "rows": [
            {"month": row.month, "sales": money(row.sales), "purchases": money(row.purchases)}
            for row in monthly.rows
        ],
        "sales_total": money(monthly.sales_total),
        "purchases_total": money(monthly.purchases_total),
    }


def _depreciation_snapshot(depreciation: DepreciationSchedule) -> dict[str, Any]:
    return {
        "lines": [
            {
                "code": line.code,
                "name": line.name,
                "acquisition_cost": money(line.acquisition_cost),
                "depreciation_expense": money(line.depreciation_expense),
                "closing_book_value": money(line.closing_book_value),
            }
            for line in depreciation.lines
        ],
        "total_depreciation": money(depreciation.total_depreciation),
        "expense_total": money(depreciation.expense_total),
    }


def financial_statements_snapshot(fs: FinancialStatements) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.FinancialStatements` into its canonical JSON shape.

    The four 面 are nested in form order: the 損益計算書 (1面) and 貸借対照表 (4面) reuse their own
    snapshots verbatim, with the 月別売上(収入)・仕入 (2面), 減価償却費の計算 (3面), and
    製造原価の計算 (4面) breakdowns alongside. Amounts are fixed-point strings (浮動小数禁止); this
    is the shape the golden harness freezes and the Vercel viewer (#25) renders.
    """
    return {
        "report": "financial_statements",
        "fiscal_year": fs.fiscal_year,
        "start_date": fs.start_date.isoformat(),
        "end_date": fs.end_date.isoformat(),
        "profit_and_loss": profit_and_loss_snapshot(fs.profit_and_loss),
        "monthly": _monthly_snapshot(fs.monthly),
        "depreciation": _depreciation_snapshot(fs.depreciation),
        "manufacturing_cost": _manufacturing_cost_snapshot(fs.manufacturing_cost),
        "balance_sheet": balance_sheet_snapshot(fs.balance_sheet),
    }


def financial_statements_text(fs: FinancialStatements) -> str:
    """Render the 青色申告決算書 as 整形テキスト for human inspection (4面を順に並べる)."""
    lines: list[str] = [
        "青色申告決算書 (Blue-Return Financial Statements)",
        f"  会計年度: {fs.fiscal_year} ({fs.start_date.isoformat()} 〜 {fs.end_date.isoformat()})",
        "",
        "■ 1面 ",
        profit_and_loss_text(fs.profit_and_loss).rstrip("\n"),
        "",
        "■ 2面 月別売上(収入)金額及び仕入金額",
    ]
    for row in fs.monthly.rows:
        lines.append(f"    {row.month}  売上 {money(row.sales)}  仕入 {money(row.purchases)}")
    lines.append(
        f"    合計  売上 {money(fs.monthly.sales_total)}  仕入 {money(fs.monthly.purchases_total)}"
    )

    lines.append("")
    lines.append("■ 3面 減価償却費の計算")
    for dep in fs.depreciation.lines:
        lines.append(
            f"    {dep.code} {dep.name}  取得価額 {money(dep.acquisition_cost)}"
            f"  本年分償却費 {money(dep.depreciation_expense)}"
            f"  期末簿価 {money(dep.closing_book_value)}"
        )
    lines.append(
        f"    本年分償却費 合計 {money(fs.depreciation.total_depreciation)}"
        f"  (PL 減価償却費 {money(fs.depreciation.expense_total)})"
    )

    lines.append("")
    lines.append("■ 4面 製造原価の計算")
    for section in (
        fs.manufacturing_cost.materials,
        fs.manufacturing_cost.labor,
        fs.manufacturing_cost.overhead,
    ):
        lines.append(f"  【{section.label}】")
        for line in section.lines:
            lines.append(f"    {line.code} {line.name}  {money(line.amount)}")
        lines.append(f"    {section.label} 計  {money(section.subtotal)}")
    lines.append(f"  当期製造費用  {money(fs.manufacturing_cost.total_manufacturing_cost)}")
    lines.append(f"  当期製品製造原価  {money(fs.manufacturing_cost.cost_of_goods_manufactured)}")

    lines.append("")
    lines.append("■ 4面 ")
    lines.append(balance_sheet_text(fs.balance_sheet).rstrip("\n"))

    return "\n".join(lines) + "\n"


# --- 不動産所得 収入側 内訳 (KOA220 data-supply, Issue #124) ----------------------


def real_estate_income_snapshot(re: RealEstateIncome) -> dict[str, Any]:
    """Turn a :class:`~ai_books.models.RealEstateIncome` into its canonical JSON shape.

    The three 収入側 内訳 (不動産所得の収入の内訳 / 地代家賃の内訳 / 借入金利子の内訳) are nested in
    form order, each followed by its 計. Amounts are fixed-point strings (浮動小数禁止); this is the shape
    the golden harness freezes and KOA220's :class:`~ai_books.etax.spec.EtaxFormatSpec` (stage 4) reads.
    """
    return {
        "report": "real_estate_income",
        "fiscal_year": re.fiscal_year,
        "start_date": re.start_date.isoformat(),
        "end_date": re.end_date.isoformat(),
        "rental_income": {
            "lines": [
                {
                    "account_code": line.account_code,
                    "property_type": line.property_type,
                    "usage": line.usage,
                    "location": line.location,
                    "tenant_address": line.tenant_address,
                    "tenant_name": line.tenant_name,
                    "contract_start_month": line.contract_start_month,
                    "contract_end_month": line.contract_end_month,
                    "rent_annual": money(line.rent_annual),
                    "key_money": money(line.key_money),
                    "right_money": money(line.right_money),
                    "renewal_fee": money(line.renewal_fee),
                    "name_change_other": money(line.name_change_other),
                    "deposit": money(line.deposit),
                    "income_subtotal": money(line.income_subtotal),
                }
                for line in re.rental_income_lines
            ],
            "rent_annual_total": money(re.rent_annual_total),
            "key_right_renewal_total": money(re.key_right_renewal_total),
            "name_change_other_total": money(re.name_change_other_total),
            "deposit_total": money(re.deposit_total),
            "gross_income": money(re.gross_income),
        },
        "rent_paid": {
            "lines": [
                {
                    "account_code": line.account_code,
                    "payee_address": line.payee_address,
                    "payee_name": line.payee_name,
                    "leased_property": line.leased_property,
                    "right_money": money(line.right_money),
                    "renewal_fee": money(line.renewal_fee),
                    "rent": money(line.rent),
                    "deductible_expense": money(line.deductible_expense),
                }
                for line in re.rent_paid_lines
            ],
            "rent_total": money(re.rent_paid_total),
            "deductible_total": money(re.rent_paid_deductible_total),
        },
        "loan_interest": {
            "lines": [
                {
                    "payee_address": line.payee_address,
                    "payee_name": line.payee_name,
                    "year_end_balance": money(line.year_end_balance),
                    "interest_paid": money(line.interest_paid),
                    "deductible_interest": money(line.deductible_interest),
                }
                for line in re.loan_interest_lines
            ],
            "year_end_balance_total": money(re.loan_year_end_balance_total),
            "interest_total": money(re.loan_interest_total),
            "deductible_total": money(re.loan_interest_deductible_total),
        },
    }


def real_estate_income_text(re: RealEstateIncome) -> str:
    """Render the 不動産所得 収入側 内訳 as 整形テキスト for human inspection (KOA220 収入側)."""
    lines: list[str] = [
        "青色申告決算書(不動産所得用) 収入側 内訳 (KOA220 data-supply)",
        f"  会計年度: {re.fiscal_year} ({re.start_date.isoformat()} 〜 {re.end_date.isoformat()})",
        "",
        "■ 不動産所得の収入の内訳",
    ]
    for line in re.rental_income_lines:
        lines.append(
            f"    {line.account_code} {line.location} (用途 {line.usage}) "
            f"賃借人 {line.tenant_name}  "
            f"契約 {line.contract_start_month}月〜{line.contract_end_month}月"
        )
        lines.append(
            f"      賃貸料年額 {money(line.rent_annual)}  礼金 {money(line.key_money)}  "
            f"権利金 {money(line.right_money)}  更新料 {money(line.renewal_fee)}  "
            f"名義書換料その他 {money(line.name_change_other)}  保証金敷金 {money(line.deposit)}"
        )
    lines.append(
        f"    計  賃貸料年額 {money(re.rent_annual_total)}  "
        f"礼金権利金更新料 {money(re.key_right_renewal_total)}  "
        f"名義書換料その他 {money(re.name_change_other_total)}  "
        f"保証金敷金 {money(re.deposit_total)}  → 収入金額 {money(re.gross_income)}"
    )

    lines.append("")
    lines.append("■ 地代家賃の内訳")
    for rent in re.rent_paid_lines:
        lines.append(
            f"    {rent.payee_name} ({rent.leased_property})  賃借料 {money(rent.rent)}  "
            f"必要経費算入額 {money(rent.deductible_expense)}"
        )
    lines.append(
        f"    計  賃借料 {money(re.rent_paid_total)}  "
        f"必要経費算入額 {money(re.rent_paid_deductible_total)}"
    )

    lines.append("")
    lines.append("■ 借入金利子の内訳")
    for loan in re.loan_interest_lines:
        lines.append(
            f"    {loan.payee_name}  期末借入金残高 {money(loan.year_end_balance)}  "
            f"本年中の借入金利子 {money(loan.interest_paid)}  "
            f"必要経費算入額 {money(loan.deductible_interest)}"
        )
    lines.append(
        f"    計  期末借入金残高 {money(re.loan_year_end_balance_total)}  "
        f"借入金利子 {money(re.loan_interest_total)}  "
        f"必要経費算入額 {money(re.loan_interest_deductible_total)}"
    )

    return "\n".join(lines) + "\n"


# --- 農業所得 収入側 内訳 (KOA240 data-supply, Issue #125) ------------------------


def agricultural_income_snapshot(ag: AgriculturalIncome) -> dict[str, Any]:
    """Turn an :class:`~ai_books.models.AgriculturalIncome` into its canonical JSON shape.

    The 収入側 内訳 (農産物の収入の内訳 / 畜産物その他 / 雑収入 / 収入金額 / 未収穫農産物 / 販売用動物 /
    果樹・牛馬等の育成費用) are nested in form order, each followed by its 計. Amounts are fixed-point strings
    (浮動小数禁止); this is the shape the golden harness freezes and KOA240's
    :class:`~ai_books.etax.spec.EtaxFormatSpec` (stage 4) reads.
    """
    return {
        "report": "agricultural_income",
        "fiscal_year": ag.fiscal_year,
        "start_date": ag.start_date.isoformat(),
        "end_date": ag.end_date.isoformat(),
        "farm_products": {
            "lines": [
                {
                    "account_code": line.account_code,
                    "category": line.category,
                    "crop_name": line.crop_name,
                    "planted_area": money(line.planted_area),
                    "harvest_quantity": money(line.harvest_quantity),
                    "opening_inventory_qty": money(line.opening_inventory_qty),
                    "opening_inventory_amount": money(line.opening_inventory_amount),
                    "sales_amount": money(line.sales_amount),
                    "home_consumption": money(line.home_consumption),
                    "closing_inventory_qty": money(line.closing_inventory_qty),
                    "closing_inventory_amount": money(line.closing_inventory_amount),
                }
                for line in ag.crop_income_lines
            ],
            "sales_total": money(ag.farm_product_sales_total),
            "home_consumption_total": money(ag.farm_product_home_consumption_total),
            "opening_inventory_total": money(ag.farm_product_opening_inventory_total),
            "closing_inventory_total": money(ag.farm_product_closing_inventory_total),
        },
        "livestock": {
            "lines": [
                {
                    "account_code": line.account_code,
                    "category_name": line.category_name,
                    "raised_count": money(line.raised_count),
                    "produced_count": money(line.produced_count),
                    "sales_amount": money(line.sales_amount),
                    "home_consumption": money(line.home_consumption),
                }
                for line in ag.livestock_income_lines
            ],
            "sales_total": money(ag.livestock_sales_total),
            "home_consumption_total": money(ag.livestock_home_consumption_total),
        },
        "misc_income": {
            "lines": [
                {
                    "account_code": line.account_code,
                    "category_name": line.category_name,
                    "amount": money(line.amount),
                }
                for line in ag.misc_income_lines
            ],
            "total": money(ag.misc_income_total),
        },
        "income": {
            "sales_amount_total": money(ag.sales_amount_total),
            "home_consumption_total": money(ag.home_consumption_total),
            "misc_income_total": money(ag.misc_income_total),
            "subtotal": money(ag.subtotal),
            "opening_inventory_total": money(ag.opening_inventory_total),
            "closing_inventory_total": money(ag.closing_inventory_total),
            "gross_income": money(ag.gross_income),
        },
        "unharvested": {
            "lines": [
                {
                    "category_name": line.category_name,
                    "opening_qty": line.opening_qty,
                    "opening_amount": money(line.opening_amount),
                    "closing_qty": line.closing_qty,
                    "closing_amount": money(line.closing_amount),
                }
                for line in ag.unharvested_lines
            ],
            "opening_total": money(ag.unharvested_opening_total),
            "closing_total": money(ag.unharvested_closing_total),
        },
        "sale_animals": {
            "lines": [
                {
                    "category_name": line.category_name,
                    "opening_qty": line.opening_qty,
                    "opening_amount": money(line.opening_amount),
                    "closing_qty": line.closing_qty,
                    "closing_amount": money(line.closing_amount),
                }
                for line in ag.sale_animal_lines
            ],
            "opening_total": money(ag.sale_animal_opening_total),
            "closing_total": money(ag.sale_animal_closing_total),
        },
        "cultivation_cost": {
            "lines": [
                {
                    "name": line.name,
                    "opening_carryover": money(line.opening_carryover),
                    "seedling_cost": money(line.seedling_cost),
                    "fertilizer_cost": money(line.fertilizer_cost),
                    "subtotal": money(line.subtotal),
                    "income_from_growing": money(line.income_from_growing),
                    "added_to_acquisition_cost": money(line.added_to_acquisition_cost),
                    "matured_acquisition_cost": money(line.matured_acquisition_cost),
                    "carryover_to_next": money(line.carryover_to_next),
                }
                for line in ag.cultivation_cost_lines
            ],
            "opening_carryover_total": money(ag.cultivation_opening_carryover_total),
            "seedling_cost_total": money(ag.cultivation_seedling_cost_total),
            "fertilizer_cost_total": money(ag.cultivation_fertilizer_cost_total),
            "subtotal_total": money(ag.cultivation_subtotal_total),
            "income_from_growing_total": money(ag.cultivation_income_from_growing_total),
            "added_to_acquisition_total": money(ag.cultivation_added_to_acquisition_total),
            "matured_acquisition_total": money(ag.cultivation_matured_acquisition_total),
            "carryover_to_next_total": money(ag.cultivation_carryover_to_next_total),
            "deductible_cultivation_cost": money(ag.deductible_cultivation_cost),
        },
    }


def agricultural_income_text(ag: AgriculturalIncome) -> str:
    """Render the 農業所得 収入側 内訳 as 整形テキスト for human inspection (KOA240 収入側)."""
    lines: list[str] = [
        "青色申告決算書(農業所得用) 収入側 内訳 (KOA240 data-supply)",
        f"  会計年度: {ag.fiscal_year} ({ag.start_date.isoformat()} 〜 {ag.end_date.isoformat()})",
        "",
        "■ 農産物の収入の内訳",
    ]
    for line in ag.crop_income_lines:
        lines.append(
            f"    [{line.category}] {line.crop_name} (作付面積 {money(line.planted_area)} / "
            f"収穫量 {money(line.harvest_quantity)})"
        )
        lines.append(
            f"      販売金額 {money(line.sales_amount)}  "
            f"家事消費等 {money(line.home_consumption)}  "
            f"期首棚卸 {money(line.opening_inventory_amount)}  "
            f"期末棚卸 {money(line.closing_inventory_amount)}"
        )
    lines.append(
        f"    計  販売金額 {money(ag.farm_product_sales_total)}  "
        f"家事消費等 {money(ag.farm_product_home_consumption_total)}  "
        f"期首棚卸 {money(ag.farm_product_opening_inventory_total)}  "
        f"期末棚卸 {money(ag.farm_product_closing_inventory_total)}"
    )

    lines.append("")
    lines.append("■ 畜産物その他")
    for stock in ag.livestock_income_lines:
        lines.append(
            f"    {stock.category_name} (飼育 {money(stock.raised_count)} / "
            f"生産 {money(stock.produced_count)})  "
            f"販売金額 {money(stock.sales_amount)}  家事消費等 {money(stock.home_consumption)}"
        )
    lines.append(
        f"    計  販売金額 {money(ag.livestock_sales_total)}  "
        f"家事消費等 {money(ag.livestock_home_consumption_total)}"
    )

    lines.append("")
    lines.append("■ 雑収入")
    for misc in ag.misc_income_lines:
        lines.append(f"    {misc.category_name}  {money(misc.amount)}")
    lines.append(f"    計  {money(ag.misc_income_total)}")

    lines.append("")
    lines.append("■ 収入金額")
    lines.append(
        f"    販売金額 {money(ag.sales_amount_total)}  "
        f"家事消費等 {money(ag.home_consumption_total)}  "
        f"雑収入 {money(ag.misc_income_total)}  → 小計 {money(ag.subtotal)}"
    )
    lines.append(
        f"    - 農産物期首棚卸 {money(ag.opening_inventory_total)}  "
        f"+ 農産物期末棚卸 {money(ag.closing_inventory_total)}  "
        f"→ 収入金額 {money(ag.gross_income)}"
    )

    lines.append("")
    lines.append("■ 未収穫農産物")
    for u in ag.unharvested_lines:
        lines.append(
            f"    {u.category_name}  期首 {u.opening_qty} {money(u.opening_amount)}  "
            f"期末 {u.closing_qty} {money(u.closing_amount)}"
        )
    lines.append(
        f"    計  期首 {money(ag.unharvested_opening_total)}  "
        f"期末 {money(ag.unharvested_closing_total)}"
    )

    lines.append("")
    lines.append("■ 販売用動物")
    for a in ag.sale_animal_lines:
        lines.append(
            f"    {a.category_name}  期首 {a.opening_qty} {money(a.opening_amount)}  "
            f"期末 {a.closing_qty} {money(a.closing_amount)}"
        )
    lines.append(
        f"    計  期首 {money(ag.sale_animal_opening_total)}  "
        f"期末 {money(ag.sale_animal_closing_total)}"
    )

    lines.append("")
    lines.append("■ 果樹・牛馬等の育成費用の計算")
    for c in ag.cultivation_cost_lines:
        lines.append(
            f"    {c.name}  前年繰越 {money(c.opening_carryover)}  "
            f"本年投下 {money(c.seedling_cost + c.fertilizer_cost)}  小計 {money(c.subtotal)}  "
            f"成熟取得価額 {money(c.matured_acquisition_cost)}  翌年繰越 {money(c.carryover_to_next)}"
        )
    lines.append(
        f"    計  小計 {money(ag.cultivation_subtotal_total)}  "
        f"成熟取得価額 {money(ag.cultivation_matured_acquisition_total)}  "
        f"翌年繰越 {money(ag.cultivation_carryover_to_next_total)}"
    )
    lines.append(f"    経費から差し引く育成費用 {money(ag.deductible_cultivation_cost)}")

    return "\n".join(lines) + "\n"

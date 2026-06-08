"""帳簿・決算書レポートの出力フォーマット (Issue #19 帳簿 / #20 損益計算書 / #21 貸借対照表 / #22 精算表 / #23 青色申告決算書).

The report *data* — 仕訳帳 (:class:`~ai_books.models.JournalBook`), 総勘定元帳
(:class:`~ai_books.models.GeneralLedger`), 損益計算書 (:class:`~ai_books.models.ProfitAndLoss`),
貸借対照表 (:class:`~ai_books.models.BalanceSheet`), and 精算表
(:class:`~ai_books.models.Worksheet`) — is produced by the repository layer. This package
turns that typed data into the concrete outputs the issues call for:

* **機械可読** — :func:`journal_book_snapshot` / :func:`general_ledger_snapshot` /
  :func:`profit_and_loss_snapshot` / :func:`balance_sheet_snapshot` / :func:`worksheet_snapshot`
  (canonical JSON dicts, the shape the golden harness freezes and the Vercel viewer #25 renders)
  and the matching :func:`journal_book_csv` / :func:`general_ledger_csv` / :func:`worksheet_csv`
  (flat CSV).
* **人間可読** — :func:`journal_book_text` / :func:`general_ledger_text` /
  :func:`profit_and_loss_text` / :func:`balance_sheet_text` / :func:`worksheet_text` (整形テキスト
  for eyeballing).

Amounts are always fixed-point strings (浮動小数禁止) so a balance never becomes a float on
the way out.
"""

from __future__ import annotations

from .format import (
    agricultural_income_snapshot,
    agricultural_income_text,
    balance_sheet_snapshot,
    balance_sheet_text,
    financial_statements_snapshot,
    financial_statements_text,
    general_ledger_csv,
    general_ledger_snapshot,
    general_ledger_text,
    journal_book_csv,
    journal_book_snapshot,
    journal_book_text,
    money,
    profit_and_loss_snapshot,
    profit_and_loss_text,
    real_estate_income_snapshot,
    real_estate_income_text,
    worksheet_csv,
    worksheet_snapshot,
    worksheet_text,
)

__all__ = [
    "agricultural_income_snapshot",
    "agricultural_income_text",
    "balance_sheet_snapshot",
    "balance_sheet_text",
    "financial_statements_snapshot",
    "financial_statements_text",
    "general_ledger_csv",
    "general_ledger_snapshot",
    "general_ledger_text",
    "journal_book_csv",
    "journal_book_snapshot",
    "journal_book_text",
    "money",
    "profit_and_loss_snapshot",
    "profit_and_loss_text",
    "real_estate_income_snapshot",
    "real_estate_income_text",
    "worksheet_csv",
    "worksheet_snapshot",
    "worksheet_text",
]

"""帳簿・決算書レポートの出力フォーマット (Issue #19 帳簿 / #20 損益計算書 / #21 貸借対照表).

The report *data* — 仕訳帳 (:class:`~ai_books.models.JournalBook`), 総勘定元帳
(:class:`~ai_books.models.GeneralLedger`), 損益計算書 (:class:`~ai_books.models.ProfitAndLoss`)
and 貸借対照表 (:class:`~ai_books.models.BalanceSheet`)
— is produced by the repository layer. This package turns that typed data into the concrete
outputs the issues call for:

* **機械可読** — :func:`journal_book_snapshot` / :func:`general_ledger_snapshot` /
  :func:`profit_and_loss_snapshot` / :func:`balance_sheet_snapshot` (canonical JSON dicts, the
  shape the golden harness freezes and the Vercel viewer #25 renders) and :func:`journal_book_csv`
  / :func:`general_ledger_csv` (flat CSV for the tabular 帳簿).
* **人間可読** — :func:`journal_book_text` / :func:`general_ledger_text` /
  :func:`profit_and_loss_text` / :func:`balance_sheet_text` (整形テキスト for eyeballing).

Amounts are always fixed-point strings (浮動小数禁止) so a balance never becomes a float on
the way out.
"""

from __future__ import annotations

from .format import (
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

__all__ = [
    "balance_sheet_snapshot",
    "balance_sheet_text",
    "general_ledger_csv",
    "general_ledger_snapshot",
    "general_ledger_text",
    "journal_book_csv",
    "journal_book_snapshot",
    "journal_book_text",
    "money",
    "profit_and_loss_snapshot",
    "profit_and_loss_text",
]

"""帳簿レポートの出力フォーマット (Issue #19 帳簿 / #22 精算表).

The report *data* — 仕訳帳 (:class:`~ai_books.models.JournalBook`), 総勘定元帳
(:class:`~ai_books.models.GeneralLedger`), and 精算表 (:class:`~ai_books.models.Worksheet`)
— is produced by the repository layer. This package turns that typed data into the
concrete outputs the issues call for:

* **機械可読** — :func:`journal_book_snapshot` / :func:`general_ledger_snapshot` /
  :func:`worksheet_snapshot` (canonical JSON dicts, the shape the golden harness freezes and
  the Vercel viewer #25 renders) and the matching :func:`journal_book_csv` /
  :func:`general_ledger_csv` / :func:`worksheet_csv` (flat CSV).
* **人間可読** — :func:`journal_book_text` / :func:`general_ledger_text` /
  :func:`worksheet_text` (整形テキスト for eyeballing).

Amounts are always fixed-point strings (浮動小数禁止) so a balance never becomes a float on
the way out.
"""

from __future__ import annotations

from .format import (
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

__all__ = [
    "general_ledger_csv",
    "general_ledger_snapshot",
    "general_ledger_text",
    "journal_book_csv",
    "journal_book_snapshot",
    "journal_book_text",
    "money",
    "worksheet_csv",
    "worksheet_snapshot",
    "worksheet_text",
]

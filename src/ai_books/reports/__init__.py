"""帳簿レポートの出力フォーマット (Issue #19).

The report *data* — 仕訳帳 (:class:`~ai_books.models.JournalBook`) and 総勘定元帳
(:class:`~ai_books.models.GeneralLedger`) — is produced by the repository layer. This
package turns that typed data into the concrete outputs the issue calls for:

* **機械可読** — :func:`journal_book_snapshot` / :func:`general_ledger_snapshot` (canonical
  JSON dicts, the shape the golden harness freezes and the Vercel viewer #25 renders) and
  :func:`journal_book_csv` / :func:`general_ledger_csv` (flat CSV).
* **人間可読** — :func:`journal_book_text` / :func:`general_ledger_text` (整形テキスト for
  eyeballing).

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
)

__all__ = [
    "general_ledger_csv",
    "general_ledger_snapshot",
    "general_ledger_text",
    "journal_book_csv",
    "journal_book_snapshot",
    "journal_book_text",
    "money",
]

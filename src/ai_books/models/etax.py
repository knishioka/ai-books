"""e-Tax 取込データ models — Issue #24.

The 青色申告決算書 (:class:`~ai_books.models.FinancialStatements`, #23) is the last *human*
form; e-Tax 連携 is the last *machine* hop. :class:`EtaxExport` is the canonical, format-
neutral intermediate between them: a flat, ordered list of :class:`EtaxRecord` — one per
e-Tax 項目 — that the CSV / XML renderers (:mod:`ai_books.etax`) turn into the concrete files
e-Tax imports.

Splitting the *mapping* (決算書 → records) from the *rendering* (records → CSV/XML) is what lets
a single data-driven format spec (:class:`~ai_books.etax.spec.EtaxFormatSpec`, versioned by
年度/様式) drive every output: change the spec, not the renderers, when the form changes.

Amounts are carried as already-serialized strings (the value as it appears in the file), not
:class:`~decimal.Decimal`, because a record's payload is whatever the spec says to emit (an
integer-yen 金額, a 科目コード, a ``YYYY-MM`` 月, a 科目名) — the typing/precision checks happen
*before* a record is built (in the export engine), so by the time a record exists its value is
final. ``e-Tax 生成物は秘密情報を含みうる`` ため、出力ファイルはリポジトリにコミットしない (運用は
README 参照)。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from .base import DomainModel


class EtaxValueKind(StrEnum):
    """A record value's kind — what the export engine validates it as before emitting it.

    Drives the schema check in :func:`ai_books.etax.export.build_etax_export`: an ``AMOUNT`` is
    validated as whole-yen and digit-bounded, a ``CODE`` as a well-formed 勘定科目コード, a
    ``MONTH`` as ``YYYY-MM``, and ``TEXT`` as a non-empty 科目名 / ラベル (when required).
    """

    AMOUNT = "amount"  # 金額 (整数円)
    CODE = "code"  # 勘定科目コード
    MONTH = "month"  # YYYY-MM
    TEXT = "text"  # 科目名・ラベル


class EtaxRecord(DomainModel):
    """One e-Tax 項目 — a single (面, 項目コード, 値) cell of the import data.

    ``form`` is the 面 区分 (machine key: ``PL`` / ``MONTHLY`` / ``DEPRECIATION`` /
    ``MANUFACTURING`` / ``BS``); ``item_code`` is the e-Tax 項目コード (e.g. ``PL010``). A scalar
    figure carries ``row=None`` and ``account_code=None``; a 科目内訳 / 月別 row carries its
    1-based ``row`` and, where the row is a 勘定科目, its ``account_code`` — so a 内訳明細 cell is
    traceable back to the account it came from. ``value`` is the final serialized payload exactly
    as it is written to the file.
    """

    form: str  # 面 区分 (PL / MONTHLY / DEPRECIATION / MANUFACTURING / BS)
    item_code: str  # e-Tax 項目コード
    label: str  # 日本語項目名
    kind: EtaxValueKind  # 値の種別 (validated before the record was built)
    value: str  # 出力値 (整数円文字列 / コード / YYYY-MM / 科目名)
    row: int | None = None  # 1-based 行番号 (内訳/月別); scalar は None
    account_code: str | None = None  # 内訳行の 勘定科目コード; それ以外は None


class EtaxExport(DomainModel):
    """e-Tax 取込データ — the 決算書 re-expressed as an ordered list of :class:`EtaxRecord`.

    ``format_version`` / ``form_id`` pin which versioned 様式 produced this (年度で変わる様式に追従
    できるよう、出力自体がどの仕様で作られたかを保持する); ``records`` are in spec order so the CSV
    rows and XML elements are byte-stable. This object is what the golden harness freezes (#17) and
    what :func:`ai_books.etax.export.render_etax` turns into CSV / XML.
    """

    format_version: str  # 様式バージョン (例: '2025')
    form_id: str  # 様式名 (例: '青色申告決算書(一般用)')
    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    records: list[EtaxRecord] = Field(default_factory=list)

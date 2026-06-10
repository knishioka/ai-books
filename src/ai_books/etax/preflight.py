"""申告前チェック (filing preflight) — 実データが e-Tax 申告可能な状態かを 1 回で判定する (#159).

これまで e-Tax 出力の検証は「export 時のスキーマ検証」(:func:`ai_books.etax.build_etax_export`)
と「CI の XSD 検証 (架空データ)」に分散しており、*自分の実データ* が申告可能かを申告前にまとめて
確認する手段が無かった。本モジュールはその中核 — データ完全性 + 決算書 → KOA210 マッピングの
dry-run — を report 層に閉じて実装し、「申告可 (ok) / 要修正 (error, 理由全件)」を返す。

判定は対象会計年度 (例 ``FY2025``) に対して:

* **error (申告ブロック)** — :class:`PreflightCheck` の ``DRAFT_ENTRY`` / ``OUT_OF_PERIOD`` /
  ``ETAX_MAPPING``。draft (未転記) 仕訳の残存、会計期間外の日付を持つ posted 仕訳、そして
  既存 export 検証 (:class:`~ai_books.errors.EtaxValidationError`) を流用した必須欄欠落・
  非整数円・桁あふれ・不正勘定科目コードを **全件** 収集する。
* **warning (申告は止めない)** — ``EMPTY_MONTH`` / ``VOIDED_ENTRIES``。posted 仕訳が 1 件も無い月
  (記帳漏れの可能性) や void 済仕訳の多発などの参考情報。空の月は休業月という正当なケースが
  あるため fail させない (誤検知になる)。

DB 読み取り・決算書生成は既存の :class:`~ai_books.db.repository.LedgerRepository` /
:class:`~ai_books.db.repository.JournalRepository` と :func:`ai_books.etax.build_etax_export`
を再利用し、新規 SQL は持たない。MCP tool としての公開と XSD 検証は別 issue (本 issue は純粋な
report 層に閉じる)。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import Field

from ai_books.aggregation import month_windows
from ai_books.db.repository import (
    MAX_PAGE_LIMIT,
    FiscalYearRepository,
    JournalRepository,
    LedgerRepository,
)
from ai_books.errors import EtaxValidationError, RecordNotFoundError
from ai_books.etax.export import build_etax_export
from ai_books.models import DomainModel, EntryStatus

if TYPE_CHECKING:
    import psycopg

    from ai_books.models import JournalEntry

#: void 済仕訳がこの件数以上あれば「多発」の参考 warning を 1 件出す (申告は止めない)。
#: 取消は通常運用でも起きうるので閾値方式にして恒常的なノイズを避ける。
VOIDED_WARNING_THRESHOLD = 5


class PreflightCheck(StrEnum):
    """preflight が報告する事象の機械可読な分類 (申告ブロックは error、参考は warning)."""

    DRAFT_ENTRY = "draft_entry"  # error: 未転記仕訳の残存
    OUT_OF_PERIOD = "out_of_period"  # error: 会計期間外の posted 仕訳
    ETAX_MAPPING = "etax_mapping"  # error: 決算書 → KOA210 マッピング検証エラー
    EMPTY_MONTH = "empty_month"  # warning: posted 仕訳が 1 件も無い月
    VOIDED_ENTRIES = "voided_entries"  # warning: void 済仕訳の多発


class PreflightIssue(DomainModel):
    """preflight が検出した 1 件の事象 (error / warning 共通の構造)。

    ``check`` が分類、``message`` が人間向けの説明 (日本語)。残りは事象種別ごとに辿れる文脈で、
    該当しないものは ``None`` のまま — 仕訳由来なら ``entry_id`` / ``voucher_no`` / ``entry_date``、
    決算書マッピング由来なら ``item_code`` / ``row``、月単位の warning なら ``month`` が埋まる。
    """

    check: PreflightCheck
    message: str
    entry_id: int | None = None
    voucher_no: str | None = None
    entry_date: date | None = None
    item_code: str | None = None
    row: str | None = None
    month: str | None = None


class PreflightReport(DomainModel):
    """会計年度 1 つ分の申告前判定 — ``status`` は error が 1 件でもあれば ``error``、無ければ ``ok``。

    ``errors`` は申告をブロックする事象、``warnings`` は止めない参考情報。両者は独立に積まれ、
    error が空でも warning は出うる (例: 空の月) — その場合も ``status`` は ``ok`` のまま。
    """

    fiscal_year: str
    start_date: date
    end_date: date
    errors: list[PreflightIssue] = Field(default_factory=list)
    warnings: list[PreflightIssue] = Field(default_factory=list)

    @property
    def status(self) -> str:
        """申告可否 — error が 1 件でもあれば ``"error"``、無ければ ``"ok"``."""
        return "error" if self.errors else "ok"

    @property
    def ok(self) -> bool:
        """申告可 (error が 1 件も無い) なら ``True``."""
        return not self.errors


def filing_preflight(conn: psycopg.Connection[Any], *, fiscal_year: str) -> PreflightReport:
    """``fiscal_year`` の実データが e-Tax 申告可能かをまとめて判定して返す (#159)。

    対象会計年度を :class:`~ai_books.db.repository.FiscalYearRepository` で解決し (未登録なら
    :class:`~ai_books.errors.RecordNotFoundError`)、以下を順に実行する:

    1. 未転記 (draft) 仕訳の残存 → error (全件)
    2. 会計期間外の日付を持つ posted 仕訳 → error (全件)
    3. 決算書 (posted のみ) → KOA210 マッピングの dry-run。
       :func:`ai_books.etax.build_etax_export` が投げる
       :class:`~ai_books.errors.EtaxValidationError` の problems を error として全件収集
    4. posted 仕訳が 1 件も無い月 → warning
    5. void 済仕訳が :data:`VOIDED_WARNING_THRESHOLD` 件以上 → warning

    どのチェックも最初の不備で止めず、全件を集めて :class:`PreflightReport` に返す。
    """
    year = FiscalYearRepository(conn).get_by_name(fiscal_year)
    if year is None:
        raise RecordNotFoundError("fiscal_year", fiscal_year)

    journals = JournalRepository(conn)
    errors: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []

    # 1. 未転記 (draft) 仕訳の残存 — 会計期間内の draft は申告をブロックする。
    for entry in _all_entries(
        journals, status=EntryStatus.DRAFT, start_date=year.start_date, end_date=year.end_date
    ):
        errors.append(
            PreflightIssue(
                check=PreflightCheck.DRAFT_ENTRY,
                message=f"未転記 (draft) の仕訳が残っています: {_entry_ref(entry)}",
                entry_id=entry.id,
                voucher_no=entry.voucher_no,
                entry_date=entry.entry_date,
            )
        )

    # 2. 会計期間外の posted 仕訳 — 全 posted を 1 回読み、期間外を error に、期間内の月を 4. 用に集める。
    posted_months: set[date] = set()
    for entry in _all_entries(journals, status=EntryStatus.POSTED):
        if entry.entry_date < year.start_date or entry.entry_date > year.end_date:
            errors.append(
                PreflightIssue(
                    check=PreflightCheck.OUT_OF_PERIOD,
                    message=(
                        f"会計期間 [{year.start_date}〜{year.end_date}] 外の日付を持つ posted 仕訳です: "
                        f"{_entry_ref(entry)}"
                    ),
                    entry_id=entry.id,
                    voucher_no=entry.voucher_no,
                    entry_date=entry.entry_date,
                )
            )
        else:
            posted_months.add(entry.entry_date.replace(day=1))

    # 3. 決算書 → KOA210 マッピングの dry-run — 検証エラーは全件 error に。
    financial_statements = LedgerRepository(conn).financial_statements(
        fiscal_year=year.name,
        start=year.start_date,
        end=year.end_date,
        status=EntryStatus.POSTED,
    )
    try:
        build_etax_export(financial_statements)
    except EtaxValidationError as exc:
        for problem in exc.problems:
            errors.append(
                PreflightIssue(
                    check=PreflightCheck.ETAX_MAPPING,
                    message=problem["message"],
                    item_code=problem.get("item_code"),
                    row=problem.get("row"),
                )
            )

    # 4. posted 仕訳が 1 件も無い月 → warning (休業月は正当なので止めない)。
    for window in month_windows(year.start_date, year.end_date):
        if window.month_start not in posted_months:
            warnings.append(
                PreflightIssue(
                    check=PreflightCheck.EMPTY_MONTH,
                    message=f"{window.label} は posted 仕訳が 1 件もありません (記帳漏れの可能性)。",
                    month=window.label,
                )
            )

    # 5. void 済仕訳の多発 → 参考 warning。
    voided_count = journals.list_entries(
        status=EntryStatus.VOIDED, start_date=year.start_date, end_date=year.end_date, limit=1
    ).total
    if voided_count >= VOIDED_WARNING_THRESHOLD:
        warnings.append(
            PreflightIssue(
                check=PreflightCheck.VOIDED_ENTRIES,
                message=f"会計期間内に void 済仕訳が {voided_count} 件あります (参考)。",
            )
        )

    return PreflightReport(
        fiscal_year=year.name,
        start_date=year.start_date,
        end_date=year.end_date,
        errors=errors,
        warnings=warnings,
    )


def _all_entries(journals: JournalRepository, **filters: Any) -> list[JournalEntry]:
    """``list_entries`` をページングしながら、フィルタに一致する全仕訳を集めて返す。

    1 ページ ``MAX_PAGE_LIMIT`` 件で ``total`` に達するまで ``offset`` を進める。preflight は
    「全件報告」が要件なので、ページ上限で取りこぼさないようにここで吸収する。
    """
    page = journals.list_entries(limit=MAX_PAGE_LIMIT, offset=0, **filters)
    entries = list(page.entries)
    while len(entries) < page.total:
        page = journals.list_entries(limit=MAX_PAGE_LIMIT, offset=len(entries), **filters)
        if not page.entries:  # 想定外だが無限ループ防止 (total と実件数の不整合)。
            break
        entries.extend(page.entries)
    return entries


def _entry_ref(entry: JournalEntry) -> str:
    """ログ/メッセージ用の仕訳参照 — 伝票番号があれば優先、無ければ id と日付。"""
    if entry.voucher_no:
        return f"{entry.voucher_no} ({entry.entry_date})"
    return f"id={entry.id} ({entry.entry_date})"

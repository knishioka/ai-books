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
を再利用する。新規 SQL は「どの会計年度にも属さない孤児 posted 仕訳の検出」と「月ごとの posted
有無判定」の 2 点に絞り (どちらも対象を DB 側で絞り込み、全 posted をメモリに載せない)、MCP tool
としての公開と XSD 検証は別 issue (本 issue は純粋な report 層に閉じる)。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from psycopg.rows import dict_row
from pydantic import Field

from ai_books.aggregation import month_windows
from ai_books.db.repository import (
    MAX_PAGE_LIMIT,
    FiscalYearRepository,
    JournalRepository,
    LedgerRepository,
)
from ai_books.errors import EtaxValidationError, RecordNotFoundError
from ai_books.etax.export import build_etax_export, render_etax_xtx
from ai_books.etax.spec import LATEST_ETAX_VERSION
from ai_books.etax.xsd import form_id_of, skip_reason, validate_xtx, xsd_available
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

    # 2. 会計期間外の posted 仕訳 — どの会計年度にも属さない孤児だけを error に。
    #    対象 FY の窓だけで判定すると他年度の正当な仕訳まで誤検知する (#170 review)。いずれの
    #    fiscal_years 期間にも入らない posted 仕訳を DB 側で絞り込むので、全 posted は読み込まない。
    for orphan in _orphan_posted_entries(conn):
        errors.append(
            PreflightIssue(
                check=PreflightCheck.OUT_OF_PERIOD,
                message=(
                    f"どの会計年度の会計期間にも属さない posted 仕訳です (会計期間外): {_row_ref(orphan)}"
                ),
                entry_id=orphan["id"],
                voucher_no=orphan["voucher_no"],
                entry_date=orphan["entry_date"],
            )
        )

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
    posted_months = _posted_months(conn, start=year.start_date, end=year.end_date)
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


def _orphan_posted_entries(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    """どの ``fiscal_years`` 期間にも入らない posted 仕訳 (孤児) を取得する (会計期間外検出)。

    対象 FY の窓で判定すると他年度の正当な仕訳まで誤検知する (#170 review) ため、いずれの会計年度
    にも属さない孤児だけを ``NOT EXISTS`` で絞る。返るのは孤児のみなので件数が増えてもメモリ安全。
    呼び出し側の row factory に依らず id/伝票番号/日付を引けるよう ``dict_row`` で読む。
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT je.id, je.voucher_no, je.entry_date
            FROM journal_entries je
            WHERE je.status = 'posted'::entry_status
              AND NOT EXISTS (
                  SELECT 1 FROM fiscal_years fy
                  WHERE je.entry_date BETWEEN fy.start_date AND fy.end_date
              )
            ORDER BY je.entry_date, je.id
            """
        )
        return cur.fetchall()


def _posted_months(conn: psycopg.Connection[Any], *, start: date, end: date) -> set[date]:
    """``[start, end]`` 内に posted 仕訳がある月の月初日の集合を返す (空の月検出用)。

    月ごとの有無だけを DB 側で ``DISTINCT`` 集約するので、対象期間の全仕訳を読み込まない。
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT date_trunc('month', je.entry_date)::date AS month_start
            FROM journal_entries je
            WHERE je.status = 'posted'::entry_status
              AND je.entry_date BETWEEN %(start)s AND %(end)s
            """,
            {"start": start, "end": end},
        )
        return {row["month_start"] for row in cur.fetchall()}


def _entry_ref(entry: JournalEntry) -> str:
    """ログ/メッセージ用の仕訳参照 — 伝票番号があれば優先、無ければ id と日付。"""
    if entry.voucher_no:
        return f"{entry.voucher_no} ({entry.entry_date})"
    return f"id={entry.id} ({entry.entry_date})"


def _row_ref(row: dict[str, Any]) -> str:
    """SQL 行 (id/voucher_no/entry_date) からメッセージ用の仕訳参照を組み立てる。"""
    if row["voucher_no"]:
        return f"{row['voucher_no']} ({row['entry_date']})"
    return f"id={row['id']} ({row['entry_date']})"


# ── 公式 XSD 検証込みの申告前チェック (MCP tool 公開層, #163) ──────────────────────
#
# filing_preflight (上の純粋な report 層) に「生成 .xtx を公式 .xsd で形式検証する」最終段を足し、
# MCP tool ``etax_preflight`` が 1 コールで「データ完全性 + 形式妥当性」を返せるようにする。XSD は
# 著作物のため非同梱 (fetch_etax_spec.py が ``.cache/etax/schema/`` に取得) — 未取得・xmlschema 未導入
# でも *fail せず* ``skipped`` を返す (設計決定: オフラインで preflight 本体の価値まで失わせない)。


class XsdValidation(DomainModel):
    """生成 ``.xtx`` を公式 ``.xsd`` で検証した結果 (#163)。

    ``status`` は ``"ok"`` (schema-valid) / ``"error"`` (形式不正あり、``errors`` に全件) /
    ``"skipped"`` (検証を実行しなかった)。``reason`` は ``skipped`` のときだけ埋まり、なぜ実行しなかったか
    (未要求 / preflight が error で .xtx 未生成 / schema 未取得 / xmlschema 未導入) と回避手順を伝える。
    """

    status: Literal["ok", "error", "skipped"]
    errors: list[str] = Field(default_factory=list)
    reason: str | None = None


class EtaxPreflightResult(DomainModel):
    """MCP tool ``etax_preflight`` の返却 — 申告前のデータ完全性 + 任意の XSD 形式検証 (#163)。

    ``status`` は申告可否 (:class:`PreflightReport` 由来 — error が 1 件でもあれば ``"error"``、無ければ
    ``"ok"``)。``errors`` / ``warnings`` は preflight 本体の結果で、``xsd_result`` が公式 .xsd 検証の
    顛末 (要求しなければ / 実行できなければ ``skipped``)。申告可否は ``status`` だけで判断でき、XSD は
    補助的な最終確認という位置づけ。
    """

    fiscal_year: str
    start_date: date
    end_date: date
    status: Literal["ok", "error"]
    errors: list[PreflightIssue] = Field(default_factory=list)
    warnings: list[PreflightIssue] = Field(default_factory=list)
    xsd_result: XsdValidation


def run_etax_preflight(
    conn: psycopg.Connection[Any],
    *,
    fiscal_year: str,
    form_version: str = LATEST_ETAX_VERSION,
    validate_xsd: bool = False,
) -> EtaxPreflightResult:
    """``fiscal_year`` の申告前チェックを 1 回で返す — データ完全性 (+ 任意で公式 XSD 形式検証) (#163)。

    1. :func:`filing_preflight` を実行 (draft 残存 / 期間外 / マッピング dry-run / 空の月 / void 多発)。
    2. error が無ければ ``form_version`` 様式の ``.xtx`` をメモリ上でレンダリング (ファイルには書かない)。
    3. ``validate_xsd=True`` のとき、``fetch_etax_spec.py`` が取得済みの ``.cache/etax/schema/`` の公式
       ``.xsd`` で形式検証する。schema 未取得 / ``xmlschema`` 未導入 / そもそも未要求 のときは *fail せず*
       :class:`XsdValidation` を ``skipped`` (理由つき) で返す。検証**失敗**のみ ``error``。

    未登録の ``fiscal_year`` は :func:`filing_preflight` が
    :class:`~ai_books.errors.RecordNotFoundError` を送出する。
    """
    report = filing_preflight(conn, fiscal_year=fiscal_year)
    xsd_result = _xsd_validation(report, conn, form_version=form_version, validate_xsd=validate_xsd)
    return EtaxPreflightResult(
        fiscal_year=report.fiscal_year,
        start_date=report.start_date,
        end_date=report.end_date,
        status=report.status,  # type: ignore[arg-type]  # PreflightReport.status は "ok"/"error"
        errors=report.errors,
        warnings=report.warnings,
        xsd_result=xsd_result,
    )


def _xsd_validation(
    report: PreflightReport,
    conn: psycopg.Connection[Any],
    *,
    form_version: str,
    validate_xsd: bool,
) -> XsdValidation:
    """生成 .xtx を公式 .xsd で検証する最終段 — 実行不能/未要求は ``skipped`` (理由つき)、失敗のみ ``error``。"""
    if not validate_xsd:
        return XsdValidation(
            status="skipped", reason="XSD 検証は要求されていません (validate_xsd=False)。"
        )
    if report.errors:
        # preflight が error → .xtx を生成しても申告には使えない。形式検証より先にデータを直す。
        return XsdValidation(
            status="skipped",
            reason="preflight が error を検出したため .xtx を生成せず、XSD 検証はスキップしました。",
        )

    # error なし → 申告に渡る .xtx をメモリ上に組み立てて公式 .xsd で形式検証する。
    year = FiscalYearRepository(conn).get_by_name(report.fiscal_year)
    assert year is not None  # filing_preflight が解決済 (未登録なら既に RecordNotFoundError)
    statements = LedgerRepository(conn).financial_statements(
        fiscal_year=year.name,
        start=year.start_date,
        end=year.end_date,
        status=EntryStatus.POSTED,
    )
    # No profile header here: the mapping dry-run above (build_etax_export) used none, so validating
    # the same no-profile export keeps the two stages consistent and keeps the report layer off
    # ~/.ai-books. The 申告者ヘッダ平文セル (#160) is supplied at actual export time (export_etax) and
    # is optional in the 様式, so a header-less .xtx is still 形式妥当 — what XSD here checks.
    export = build_etax_export(statements, version=form_version)
    xtx = render_etax_xtx(export)

    form_id = form_id_of(xtx)
    if not xsd_available(form_id):
        return XsdValidation(status="skipped", reason=skip_reason())
    try:
        errors = validate_xtx(xtx)
    except ImportError:
        return XsdValidation(
            status="skipped",
            reason="XSD 検証ライブラリ (xmlschema, dev 依存) が未導入のためスキップしました。",
        )
    if errors:
        return XsdValidation(status="error", errors=errors)
    return XsdValidation(status="ok")

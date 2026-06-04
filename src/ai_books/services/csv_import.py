"""Bank / credit-card CSV → draft 仕訳 import (Issue #14).

The 取込 path turns a bank or credit-card statement into *draft* journal entries so a
human/AI can review and post them (#13) — imports never confirm the books directly
(必ず status=draft で起票). The work splits in two:

* :func:`plan_import` is **pure** (no DB): it parses the CSV against a known format,
  maps each row to a two-line, self-balancing entry, infers the 相手科目 from the 摘要
  via keyword rules (falling back to a suspense 科目 when nothing matches), and
  fingerprints the source row into a deterministic ``import_hash`` for 二重取込検知.
  Being DB-free, it is what the golden-snapshot test pins.
* :class:`CsvImportService` is the **persistence** half: it skips rows whose
  ``import_hash`` is already stored, then creates each remaining entry through the
  production :class:`~ai_books.services.JournalService` — so every imported draft goes
  through the *same* balance / account / period validation as a hand-entered one — and
  returns an :class:`~ai_books.models.ImportSummary` (件数 / 重複 / 未割当).

Counter-account inference is intentionally the minimal version the issue calls for:
摘要ベースのキーワードルール. History-learning (過去仕訳からの学習) is left to a
follow-up; an unmatched row lands in suspense rather than guessing.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import psycopg

from ai_books.db.repository import JournalRepository
from ai_books.errors import CsvImportError
from ai_books.models import (
    EntrySide,
    ImportSummary,
    JournalEntryInput,
    JournalLineInput,
)
from ai_books.services.journal import DEFAULT_ACTOR, JournalService

# --- suspense 科目 (相手科目未確定の退避先) ------------------------------------

#: 出金で相手科目が確定できない明細の退避先 (借方)。seed/accounts.py の 仮払金。
SUSPENSE_DEBIT_CODE = "1210"  # 仮払金 (asset)
#: 入金で相手科目が確定できない明細の退避先 (貸方)。seed/accounts.py の 仮受金。
SUSPENSE_CREDIT_CODE = "2170"  # 仮受金 (liability)


# --- 摘要ベースの相手科目推定ルール (最小版) ----------------------------------

#: 出金 (相手科目=借方, 費用/資産) の 摘要→科目コード ルール。先頭一致が優先。
OUTFLOW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("7130", ("電気", "電力", "ガス", "水道", "東京電力", "東京ガス", "TEPCO", "でんき")),
    (
        "7150",
        ("通信", "NTT", "ドコモ", "ソフトバンク", "SOFTBANK", "インターネット", "携帯", "モバイル"),
    ),
    ("7140", ("JR", "鉄道", "交通", "SUICA", "PASMO", "タクシー", "航空", "ANA", "JAL")),
    ("7200", ("AMAZON", "アマゾン", "ヨドバシ", "ビックカメラ", "文具", "消耗品")),
    ("7250", ("家賃", "賃料", "地代")),
    ("7170", ("接待", "会食")),
    ("7290", ("手数料",)),
)

#: 入金 (相手科目=貸方, 収益/負債) の 摘要→科目コード ルール。先頭一致が優先。
INFLOW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("4110", ("売上", "報酬", "請求", "ご入金", "振込")),
    ("8110", ("受取利息", "利息")),
)


def _match_rules(description: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    """Return the first rule's account code whose keyword appears in ``description``.

    Matching is case-insensitive (ASCII upper-folded; Japanese substrings compare as-is).
    ``None`` when nothing matches — the caller then routes the line to suspense.
    """
    haystack = description.upper()
    for code, keywords in rules:
        if any(keyword.upper() in haystack for keyword in keywords):
            return code
    return None


# --- CSV フォーマット定義 + 自動判定 ------------------------------------------

#: 試行する日付フォーマット (ISO / スラッシュ / ドット / 和式表記)。
_DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%Y年%m月%d日")


@dataclass(frozen=True)
class CsvFormat:
    """A named column mapping for one bank/CC statement shape.

    ``kind`` decides how a row's amount maps onto the imported account's side:

    * ``bank`` — separate 出金/入金 columns. A 入金 debits the (asset) account, a
      出金 credits it.
    * ``card`` — a single 利用金額 column of charges. A charge credits the
      (liability, 未払金) account; a negative value (返金) debits it.

    Each ``*_aliases`` tuple lists header labels that map to that logical field, so one
    preset covers the small wording differences across real exports.
    """

    name: str
    kind: Literal["bank", "card"]
    date_aliases: tuple[str, ...]
    description_aliases: tuple[str, ...]
    withdrawal_aliases: tuple[str, ...] = ()
    deposit_aliases: tuple[str, ...] = ()
    amount_aliases: tuple[str, ...] = ()
    balance_aliases: tuple[str, ...] = field(default=())


#: Built-in presets, tried in order for ``auto`` detection.
PRESETS: tuple[CsvFormat, ...] = (
    CsvFormat(
        name="generic_bank",
        kind="bank",
        date_aliases=("日付", "取引日", "お取引日", "date"),
        description_aliases=("摘要", "内容", "取引内容", "お取引内容", "description", "memo"),
        withdrawal_aliases=("出金", "お支払金額", "支払金額", "引出金額", "withdrawal", "debit"),
        deposit_aliases=("入金", "お預り金額", "預入金額", "deposit", "credit"),
        balance_aliases=("残高", "差引残高", "balance"),
    ),
    CsvFormat(
        name="generic_card",
        kind="card",
        date_aliases=("利用日", "ご利用日", "date"),
        description_aliases=(
            "利用店名",
            "ご利用店名",
            "利用内容",
            "ご利用先",
            "利用先",
            "摘要",
            "description",
        ),
        amount_aliases=("利用金額", "ご利用金額", "金額", "amount"),
    ),
)

PRESETS_BY_NAME: dict[str, CsvFormat] = {fmt.name: fmt for fmt in PRESETS}


def _find_column(fieldnames: list[str], aliases: tuple[str, ...]) -> str | None:
    """Return the actual header in ``fieldnames`` matching one of ``aliases`` (or None).

    Comparison is whitespace-insensitive and case-insensitive so a 'Date' header still
    matches the ``date`` alias and incidental spaces never hide a column.
    """
    normalized = {name.strip().casefold(): name for name in fieldnames}
    for alias in aliases:
        actual = normalized.get(alias.strip().casefold())
        if actual is not None:
            return actual
    return None


def _format_matches(fmt: CsvFormat, fieldnames: list[str]) -> bool:
    """True when ``fieldnames`` carries the columns ``fmt`` needs to map a row."""
    if _find_column(fieldnames, fmt.date_aliases) is None:
        return False
    if _find_column(fieldnames, fmt.description_aliases) is None:
        return False
    if fmt.kind == "bank":
        has_withdrawal = _find_column(fieldnames, fmt.withdrawal_aliases) is not None
        has_deposit = _find_column(fieldnames, fmt.deposit_aliases) is not None
        return has_withdrawal or has_deposit
    return _find_column(fieldnames, fmt.amount_aliases) is not None


def _resolve_format(csv_format: str, fieldnames: list[str]) -> CsvFormat:
    """Pick the :class:`CsvFormat` to use, honouring an explicit name or auto-detecting.

    ``auto`` returns the first preset whose required columns are all present; a named
    format is looked up and then checked against the header so a mismatch fails loudly
    rather than silently mis-mapping.
    """
    if csv_format != "auto":
        fmt = PRESETS_BY_NAME.get(csv_format)
        if fmt is None:
            known = ", ".join(PRESETS_BY_NAME)
            raise CsvImportError(f"unknown csv_format {csv_format!r}; known formats: {known}")
        if not _format_matches(fmt, fieldnames):
            raise CsvImportError(
                f"CSV header does not match format {csv_format!r}; columns: {', '.join(fieldnames)}"
            )
        return fmt
    for fmt in PRESETS:
        if _format_matches(fmt, fieldnames):
            return fmt
    raise CsvImportError("could not auto-detect a CSV format; columns: " + ", ".join(fieldnames))


# --- 数値 / 日付 パース --------------------------------------------------------

#: 金額文字列から除去する装飾 (通貨記号 / 桁区切り / 単位 / 空白)。
_AMOUNT_STRIP = str.maketrans({"¥": None, "￥": None, ",": None, "円": None, " ": None, "　": None})


def _parse_amount(raw: str | None) -> Decimal | None:
    """Parse a money cell to ``Decimal`` (``None`` when the cell is blank).

    Strips currency symbols, 桁区切りカンマ, 円 and whitespace first. Raises
    :class:`ValueError` for a non-numeric, non-blank cell so the caller can attach the
    row number.
    """
    if raw is None:
        return None
    cleaned = raw.strip().translate(_AMOUNT_STRIP)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"金額として解釈できません: {raw!r}") from exc


def _parse_date(raw: str | None) -> date:
    """Parse a date cell against the accepted formats; raise ``ValueError`` if none fit."""
    from datetime import datetime

    text = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日付として解釈できません: {raw!r}")


# --- 行 → 計画 (pure) ----------------------------------------------------------


@dataclass(frozen=True)
class PlannedImport:
    """One CSV row mapped to a self-balancing draft entry, before persistence.

    ``import_hash`` fingerprints the source row (二重取込検知); ``to_suspense`` is True
    when the 相手科目 could not be inferred and the line was routed to a suspense 科目.
    """

    import_hash: str
    to_suspense: bool
    entry: JournalEntryInput


def _row_sides(
    fmt: CsvFormat, row: dict[str, str], columns: dict[str, str | None], row_no: int
) -> tuple[EntrySide, EntrySide, Decimal]:
    """Resolve (imported-account side, counter side, positive amount) for one row.

    Encapsulates the bank vs card sign convention; raises :class:`CsvImportError`
    (with the row number) when a row carries no usable amount or an ambiguous one.
    """
    if fmt.kind == "bank":
        try:
            withdrawal = (
                _parse_amount(row.get(columns["withdrawal"] or ""))
                if columns["withdrawal"]
                else None
            )
            deposit = (
                _parse_amount(row.get(columns["deposit"] or "")) if columns["deposit"] else None
            )
        except ValueError as exc:
            raise CsvImportError(str(exc), row=row_no) from exc
        has_w = withdrawal is not None and withdrawal != 0
        has_d = deposit is not None and deposit != 0
        if has_w and has_d:
            raise CsvImportError("出金と入金の両方に金額があります", row=row_no)
        if has_d:
            return EntrySide.DEBIT, EntrySide.CREDIT, abs(deposit)  # type: ignore[arg-type]
        if has_w:
            return EntrySide.CREDIT, EntrySide.DEBIT, abs(withdrawal)  # type: ignore[arg-type]
        raise CsvImportError("出金・入金のいずれにも金額がありません", row=row_no)

    # card: a charge increases the 未払金 (liability) → credit the imported account.
    try:
        amount = _parse_amount(row.get(columns["amount"] or "")) if columns["amount"] else None
    except ValueError as exc:
        raise CsvImportError(str(exc), row=row_no) from exc
    if amount is None or amount == 0:
        raise CsvImportError("利用金額がありません", row=row_no)
    if amount < 0:  # 返金 (refund): reverse the charge.
        return EntrySide.DEBIT, EntrySide.CREDIT, abs(amount)
    return EntrySide.CREDIT, EntrySide.DEBIT, amount


def _import_hash(
    account_code: str,
    fmt: CsvFormat,
    row_index: int,
    entry_date: date,
    bank_side: EntrySide,
    amount: Decimal,
    description: str,
    balance_raw: str,
) -> str:
    """Deterministic fingerprint of a source row for 二重取込検知.

    Includes the row's position and (when present) its 残高 so two *legitimately*
    identical transactions in one file stay distinct, while re-importing the same file
    reproduces the same hashes and is skipped wholesale.
    """
    canonical = "\x1f".join(
        (
            account_code,
            fmt.name,
            str(row_index),
            entry_date.isoformat(),
            bank_side.value,
            format(amount, "f"),
            description,
            balance_raw,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_import(csv_text: str, account_code: str, csv_format: str = "auto") -> list[PlannedImport]:
    """Parse ``csv_text`` into a list of self-balancing draft entries (no DB access).

    Resolves the format (explicit or auto-detected), then maps each data row to a
    two-line entry: the imported account on one side, the inferred 相手科目 (or a
    suspense 科目) on the other, for the same amount — so every entry balances by
    construction. Raises :class:`CsvImportError` for a missing header, an unmatched
    format, or a row whose 日付/金額 cannot be parsed.
    """
    # Strip a leading UTF-8 BOM a spreadsheet export may have left before the header,
    # so the first column name matches its alias rather than carrying a hidden ﻿.
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))
    fieldnames = [name for name in (reader.fieldnames or []) if name is not None]
    if not fieldnames:
        raise CsvImportError("CSV にヘッダ行がありません")

    fmt = _resolve_format(csv_format, fieldnames)
    columns: dict[str, str | None] = {
        "date": _find_column(fieldnames, fmt.date_aliases),
        "description": _find_column(fieldnames, fmt.description_aliases),
        "withdrawal": _find_column(fieldnames, fmt.withdrawal_aliases),
        "deposit": _find_column(fieldnames, fmt.deposit_aliases),
        "amount": _find_column(fieldnames, fmt.amount_aliases),
        "balance": _find_column(fieldnames, fmt.balance_aliases),
    }

    planned: list[PlannedImport] = []
    for row_index, raw_row in enumerate(reader):
        row = {k: (v or "") for k, v in raw_row.items() if k is not None}
        # Skip a wholly-blank trailing line (spreadsheets often append one).
        if not any(value.strip() for value in row.values()):
            continue
        row_no = row_index + 1  # 1-based, header excluded

        try:
            entry_date = _parse_date(row.get(columns["date"] or ""))
        except ValueError as exc:
            raise CsvImportError(str(exc), row=row_no) from exc
        description = (row.get(columns["description"] or "") or "").strip()
        balance_raw = (
            (row.get(columns["balance"] or "") or "").strip() if columns["balance"] else ""
        )

        bank_side, counter_side, amount = _row_sides(fmt, row, columns, row_no)

        rules = OUTFLOW_RULES if counter_side is EntrySide.DEBIT else INFLOW_RULES
        counter_code = _match_rules(description, rules)
        to_suspense = counter_code is None
        if counter_code is None:
            counter_code = (
                SUSPENSE_DEBIT_CODE if counter_side is EntrySide.DEBIT else SUSPENSE_CREDIT_CODE
            )

        bank_line = JournalLineInput(
            account_code=account_code,
            side=bank_side,
            amount=amount,
            line_description=description or None,
        )
        counter_line = JournalLineInput(
            account_code=counter_code,
            side=counter_side,
            amount=amount,
            line_description=description or None,
        )
        # Order lines debit-first for a stable, readable shape.
        lines = (
            [bank_line, counter_line] if bank_side is EntrySide.DEBIT else [counter_line, bank_line]
        )
        entry = JournalEntryInput(
            entry_date=entry_date,
            description=description or None,
            source=f"csv:{fmt.name}",
            lines=lines,
        )
        import_hash = _import_hash(
            account_code, fmt, row_index, entry_date, bank_side, amount, description, balance_raw
        )
        planned.append(PlannedImport(import_hash=import_hash, to_suspense=to_suspense, entry=entry))

    return planned


# --- 永続化 -------------------------------------------------------------------


class CsvImportService:
    """Persist a planned CSV import as draft 仕訳, skipping already-imported rows."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn
        self._journals = JournalRepository(conn)
        self._service = JournalService(conn)

    def import_csv(
        self,
        csv_text: str,
        *,
        account_code: str,
        csv_format: str = "auto",
        actor: str = DEFAULT_ACTOR,
    ) -> ImportSummary:
        """Import ``csv_text`` into draft entries and return the run summary (件数/重複/未割当).

        Rows whose ``import_hash`` is already stored are skipped (二重取込検知); each new
        row is created through :class:`JournalService` so it passes the same server-side
        validation as a hand-entered entry. The batch is one transaction — a row that
        fails validation rolls the whole import back rather than half-importing a
        statement. Generated entries are always ``draft``; confirmation goes through #13.
        """
        plan = plan_import(csv_text, account_code, csv_format)
        existing = self._journals.existing_import_hashes([p.import_hash for p in plan])

        entry_ids: list[int] = []
        duplicates = 0
        unassigned = 0
        seen: set[str] = set()

        if plan:
            with self._conn.transaction():
                for planned in plan:
                    if planned.import_hash in existing or planned.import_hash in seen:
                        duplicates += 1
                        continue
                    try:
                        stored = self._service.create_entry(
                            planned.entry,
                            actor=actor,
                            tool_name="import_transactions_csv",
                            import_hash=planned.import_hash,
                        )
                    except psycopg.errors.UniqueViolation:
                        # A concurrent import already stored this row — count as duplicate.
                        duplicates += 1
                        continue
                    seen.add(planned.import_hash)
                    if stored.id is not None:
                        entry_ids.append(stored.id)
                    if planned.to_suspense:
                        unassigned += 1

        return ImportSummary(
            total_rows=len(plan),
            imported=len(entry_ids),
            duplicates=duplicates,
            unassigned=unassigned,
            entry_ids=entry_ids,
        )

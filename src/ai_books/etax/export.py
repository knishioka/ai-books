"""決算書 → e-Tax 取込データ: mapping, schema validation, and CSV/XML rendering — Issue #24.

Three responsibilities, kept separate so the year-volatile part stays in the spec
(:mod:`ai_books.etax.spec`) rather than in code:

* :func:`build_etax_export` — walk the versioned 様式 spec over the 決算書 snapshot, validate every
  項目 (必須・整数円・桁数・コード・月), and produce the format-neutral
  :class:`~ai_books.models.EtaxExport`. A schema fault raises :class:`~ai_books.errors.EtaxValidationError`
  with *all* problems (必須項目欠落・不正コードを検出してエラーにする).
* :func:`render_etax` (+ :func:`render_etax_csv` / :func:`render_etax_xml`) — turn that export into
  the concrete CSV / XML files e-Tax imports. Pure functions of the export, so the same records
  always serialize byte-for-byte.
* :func:`etax_export_snapshot` — the canonical JSON shape the golden harness (#17) freezes; the
  CSV/XML are deterministic functions of it.

:func:`export_etax` is the headline one-call entry (決算書 → rendered string in the requested
format). Amounts are emitted as **整数円** (e-Tax 取込は円単位): a 金額 carrying any 端数 (sen) is a
validation error rather than being silently rounded.

生成された CSV/XML は事業者の確定数値 (秘密情報) を含みうるため、リポジトリにコミットしない
(運用は README 参照)。
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from ai_books.errors import EtaxValidationError
from ai_books.models import EtaxExport, EtaxRecord, EtaxValueKind, FinancialStatements
from ai_books.reports import financial_statements_snapshot

from .spec import (
    LATEST_ETAX_VERSION,
    MISSING,
    EtaxFixedSection,
    EtaxScalarField,
    EtaxSection,
    EtaxSectionField,
    get_format_spec,
    resolve_list,
    resolve_scalar,
)

#: A well-formed 勘定科目コード — 3〜4 桁の数字 (the chart uses 4-digit codes).
_ACCOUNT_CODE_RE = re.compile(r"\d{3,4}")
#: A well-formed 月 — ``YYYY-MM``.
_MONTH_RE = re.compile(r"\d{4}-\d{2}")


class EtaxFormat(StrEnum):
    """The concrete file formats e-Tax 取込データ can be rendered to."""

    CSV = "csv"
    XML = "xml"


def parse_etax_format(value: str) -> EtaxFormat:
    """Parse a format string into :class:`EtaxFormat`, raising a clear ``ValueError`` otherwise."""
    try:
        return EtaxFormat(value)
    except ValueError as exc:
        allowed = ", ".join(f.value for f in EtaxFormat)
        raise ValueError(f"format must be one of: {allowed}; got {value!r}") from exc


# ── validation + rendering of a single value ─────────────────────────────────────


def _validate_value(
    raw: Any, kind: EtaxValueKind, *, required: bool, max_int_digits: int
) -> tuple[str | None, str | None]:
    """Validate one cell against its kind; return ``(rendered, problem)`` (exactly one non-None).

    A missing/empty value is a problem only when ``required``; otherwise it renders to ``""``. The
    ``rendered`` form is the final on-file payload (整数円 for 金額, the value verbatim otherwise).
    """
    if raw is MISSING or raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if required:
            return None, "required value is missing or empty"
        return "", None

    text = str(raw).strip()
    if kind is EtaxValueKind.AMOUNT:
        return _render_amount(text, max_int_digits)
    if kind is EtaxValueKind.CODE:
        if not _ACCOUNT_CODE_RE.fullmatch(text):
            return None, f"invalid 勘定科目コード {text!r} (expected 3-4 digits)"
        return text, None
    if kind is EtaxValueKind.MONTH:
        if not _MONTH_RE.fullmatch(text):
            return None, f"invalid month {text!r} (expected YYYY-MM)"
        return text, None
    # TEXT — already known non-empty here.
    return text, None


def _render_amount(text: str, max_int_digits: int) -> tuple[str | None, str | None]:
    """Validate a 金額 as whole-yen within ``max_int_digits`` and render it as an integer string."""
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, f"invalid amount {text!r} (not a number)"
    if amount != amount.to_integral_value():
        return None, f"amount {text!r} is not whole yen (端数は不可)"
    integer = int(amount)
    if len(str(abs(integer))) > max_int_digits:
        return None, f"amount {text!r} exceeds {max_int_digits} integer digits"
    return str(integer), None


# ── 決算書 → EtaxExport ───────────────────────────────────────────────────────────


def build_etax_export(
    financial_statements: FinancialStatements, *, version: str = LATEST_ETAX_VERSION
) -> EtaxExport:
    """Map a 青色申告決算書 to e-Tax records under the ``version`` 様式, validating every 項目.

    Reads the 決算書 through its canonical snapshot (so the e-Tax layer depends on the frozen JSON
    shape, not model internals), pulls each spec field/section by path, and validates as it goes.
    Every fault is collected; if any exist, raises :class:`~ai_books.errors.EtaxValidationError`
    with the full list (no partial export is returned). Otherwise returns the ordered
    :class:`~ai_books.models.EtaxExport`.
    """
    spec = get_format_spec(version)
    snapshot = financial_statements_snapshot(financial_statements)
    records: list[EtaxRecord] = []
    problems: list[dict[str, str]] = []

    for item in spec.items:
        if isinstance(item, EtaxScalarField):
            _emit_scalar(item, snapshot, records, problems)
        elif isinstance(item, EtaxSection):
            for row_index, row in enumerate(resolve_list(snapshot, item.source), start=1):
                _emit_section_row(item.form, item.fields, row, row_index, records, problems)
        else:  # EtaxFixedSection
            _emit_fixed_section(item, snapshot, records, problems)

    if problems:
        raise EtaxValidationError(problems)

    return EtaxExport(
        format_version=spec.version,
        form_id=spec.form_id,
        fiscal_year=financial_statements.fiscal_year,
        start_date=financial_statements.start_date,
        end_date=financial_statements.end_date,
        records=records,
    )


def _emit_scalar(
    field: EtaxScalarField,
    snapshot: dict[str, Any],
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> None:
    """Validate one scalar field and append its record, or record a problem."""
    raw = resolve_scalar(snapshot, field.source)
    rendered, problem = _validate_value(
        raw, field.kind, required=field.required, max_int_digits=field.max_int_digits
    )
    if problem is not None:
        problems.append({"item_code": field.item_code, "row": "", "message": problem})
        return
    assert rendered is not None
    records.append(
        EtaxRecord(
            form=field.form,
            item_code=field.item_code,
            label=field.label,
            kind=field.kind,
            value=rendered,
        )
    )


def _emit_section_row(
    form: str,
    fields: tuple[EtaxSectionField, ...],
    row: dict[str, Any],
    row_index: int,
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> None:
    """Validate one 内訳 row's columns and append their records, or record problems.

    The row's 勘定科目コード (the field tagged ``is_account_code``) is resolved first so every record
    of the row can carry it for traceability — even the 金額 / 科目名 cells.
    """
    account_code = _row_account_code(fields, row)
    for field in fields:
        raw = row.get(field.source, MISSING)
        rendered, problem = _validate_value(
            raw, field.kind, required=field.required, max_int_digits=field.max_int_digits
        )
        if problem is not None:
            problems.append(
                {"item_code": field.item_code, "row": str(row_index), "message": problem}
            )
            continue
        assert rendered is not None
        records.append(
            EtaxRecord(
                form=form,
                item_code=field.item_code,
                label=field.label,
                kind=field.kind,
                value=rendered,
                row=row_index,
                account_code=account_code,
            )
        )


def _row_account_code(fields: tuple[EtaxSectionField, ...], row: dict[str, Any]) -> str | None:
    """The row's 勘定科目コード (its ``is_account_code`` column), if present and a string."""
    for field in fields:
        if field.is_account_code:
            value = row.get(field.source)
            return value if isinstance(value, str) else None
    return None


# ── 実 様式 固定勘定科目行 (EtaxFixedSection) — route snapshot lines by コード (#78) ──


def _signed_amount(value: Any, sign: str) -> Decimal | None:
    """Parse a snapshot amount and apply ``sign`` (``as_is`` / ``abs`` / ``neg``); ``None`` if NaN."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if sign == "abs":
        return abs(amount)
    if sign == "neg":
        return -amount
    return amount


def _emit_fixed_section(
    section: EtaxFixedSection,
    snapshot: dict[str, Any],
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> None:
    """Route a 固定勘定科目行 section: lines → fixed 項目コード (summed), spillover → 追加科目枠.

    Each line is matched to the :class:`~ai_books.etax.spec.EtaxFixedRow` whose ``account_codes``
    contain the line's ``code`` (amounts for the same target row are summed, with the row's
    ``sign``). A line whose ``code`` is malformed is rejected (不正コード検出); a line matching no
    fixed row spills into the 追加科目枠 — ``accumulate`` mode sums them into one ``overflow_code``,
    ``slots`` mode fills ``overflow_max`` repeating slots and **errors on overflow** (未分類検出).
    """
    code_to_row = {code: row for row in section.rows for code in row.account_codes}
    sums: dict[str, Decimal] = {}
    sole_code: dict[str, str | None] = {}
    overflow: list[dict[str, Any]] = []

    lines = [row for source in section.sources for row in resolve_list(snapshot, source)]
    for line in lines:
        code = line.get("code")
        if isinstance(code, str) and not _ACCOUNT_CODE_RE.fullmatch(code):
            problems.append(
                {
                    "item_code": section.section_code,
                    "row": "",
                    "message": f"invalid 勘定科目コード {code!r} (expected 3-4 digits)",
                }
            )
            continue
        fixed = code_to_row.get(code) if isinstance(code, str) else None
        if fixed is None:
            overflow.append(line)
            continue
        amount = _signed_amount(line.get(section.value_field), fixed.sign)
        if amount is None:
            problems.append(
                {
                    "item_code": fixed.item_code,
                    "row": "",
                    "message": f"invalid amount {line.get(section.value_field)!r} (not a number)",
                }
            )
            continue
        first_contribution = fixed.item_code not in sums
        sums[fixed.item_code] = sums.get(fixed.item_code, Decimal(0)) + amount
        # account_code が辿れるのは その行に寄与した 科目が1つだけのとき (合算行は None).
        sole_code[fixed.item_code] = code if first_contribution else None

    for row in section.rows:
        if row.item_code in sums:
            _emit_fixed_value(
                section,
                row.item_code,
                row.label,
                sums[row.item_code],
                records,
                problems,
                account_code=sole_code.get(row.item_code),
            )

    _emit_overflow(section, overflow, records, problems)


def _emit_overflow(
    section: EtaxFixedSection,
    overflow: list[dict[str, Any]],
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> None:
    """Emit the section's 追加科目枠 (accumulate into one cell, or fill rep-limited slots)."""
    if not overflow or section.overflow_code is None:
        if overflow:
            for line in overflow:
                problems.append(
                    {
                        "item_code": section.section_code,
                        "row": "",
                        "message": f"未分類科目 {line.get('code')!r} ({line.get('name')!r}): "
                        "対応する固定行も追加科目枠も無い",
                    }
                )
        return

    if section.overflow_mode == "accumulate":
        total = Decimal(0)
        for line in overflow:
            amount = _signed_amount(line.get(section.value_field), "as_is")
            if amount is None:
                problems.append(
                    {
                        "item_code": section.overflow_code,
                        "row": "",
                        "message": f"invalid amount {line.get(section.value_field)!r} (not a number)",
                    }
                )
                continue
            total += amount
        _emit_fixed_value(
            section, section.overflow_code, section.overflow_label, total, records, problems
        )
        return

    # slots mode — each 未分類科目 takes the next 追加科目枠 slot; overflow ⇒ 未分類エラー.
    for slot, line in enumerate(overflow, start=1):
        if slot > section.overflow_max:
            problems.append(
                {
                    "item_code": section.overflow_code,
                    "row": str(slot),
                    "message": f"追加科目枠 ({section.overflow_max}) 超過: 未分類科目 "
                    f"{line.get('code')!r} ({line.get('name')!r})",
                }
            )
            continue
        amount = _signed_amount(line.get(section.value_field), "as_is")
        if amount is None:
            problems.append(
                {
                    "item_code": section.overflow_code,
                    "row": str(slot),
                    "message": f"invalid amount {line.get(section.value_field)!r} (not a number)",
                }
            )
            continue
        name = line.get("name")
        code = line.get("code")
        _emit_fixed_value(
            section,
            section.overflow_code,
            name if isinstance(name, str) and name else section.overflow_label,
            amount,
            records,
            problems,
            row=slot,
            account_code=code if isinstance(code, str) else None,
        )


def _emit_fixed_value(
    section: EtaxFixedSection,
    item_code: str,
    label: str,
    amount: Decimal,
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
    *,
    row: int | None = None,
    account_code: str | None = None,
) -> None:
    """Validate a summed 固定行 amount and append its record, or record a problem."""
    rendered, problem = _validate_value(
        amount, section.kind, required=True, max_int_digits=section.max_int_digits
    )
    if problem is not None:
        problems.append({"item_code": item_code, "row": str(row or ""), "message": problem})
        return
    assert rendered is not None
    records.append(
        EtaxRecord(
            form=section.form,
            item_code=item_code,
            label=label,
            kind=section.kind,
            value=rendered,
            row=row,
            account_code=account_code,
        )
    )


# ── EtaxExport → CSV / XML / snapshot ─────────────────────────────────────────────

_ETAX_CSV_HEADER = ["面", "項目コード", "項目名", "行", "勘定科目コード", "値"]


def render_etax_csv(export: EtaxExport) -> str:
    """Render the e-Tax 取込データ as CSV — one row per record, in spec order.

    Columns: 面 / 項目コード / 項目名 / 行 / 勘定科目コード / 値. 行 and 勘定科目コード are blank for
    scalar 項目; both are filled for 内訳/月別 cells so a row is traceable to its 勘定科目.
    """
    # newline="" so csv.writer controls line endings (avoids \r\r\n on Windows — see csv docs).
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_ETAX_CSV_HEADER)
    for record in export.records:
        writer.writerow(
            [
                record.form,
                record.item_code,
                record.label,
                "" if record.row is None else str(record.row),
                record.account_code or "",
                record.value,
            ]
        )
    return buffer.getvalue()


def render_etax_xml(export: EtaxExport) -> str:
    """Render the e-Tax 取込データ as XML — a ``<record>`` per cell under ``<etaxExport>``.

    The root carries the 様式 version / 様式名 / 会計年度 / 期間 so a consumer knows which 仕様 produced
    the file. ``row`` / ``accountCode`` attributes appear only on 内訳/月別 cells. Output is
    deterministic (records in spec order, 2-space indented, UTF-8 declaration prepended).
    """
    root = ET.Element(
        "etaxExport",
        {
            "version": export.format_version,
            "form": export.form_id,
            "fiscalYear": export.fiscal_year,
            "startDate": export.start_date.isoformat(),
            "endDate": export.end_date.isoformat(),
        },
    )
    for record in export.records:
        attrib = {"form": record.form, "itemCode": record.item_code, "label": record.label}
        if record.row is not None:
            attrib["row"] = str(record.row)
        if record.account_code is not None:
            attrib["accountCode"] = record.account_code
        element = ET.SubElement(root, "record", attrib)
        element.text = record.value
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def etax_export_snapshot(export: EtaxExport) -> dict[str, Any]:
    """Turn an :class:`~ai_books.models.EtaxExport` into its canonical JSON shape.

    This is the report-agnostic structure the golden harness freezes (#17): the 様式 identity plus
    every record (面 / 項目コード / 種別 / 行 / 勘定科目コード / 値) in spec order. The CSV and XML
    renderings are deterministic functions of this, so freezing it pins all three outputs.
    """
    return {
        "report": "etax_export",
        "format_version": export.format_version,
        "form_id": export.form_id,
        "fiscal_year": export.fiscal_year,
        "start_date": export.start_date.isoformat(),
        "end_date": export.end_date.isoformat(),
        "records": [
            {
                "form": record.form,
                "item_code": record.item_code,
                "label": record.label,
                "kind": record.kind.value,
                "row": record.row,
                "account_code": record.account_code,
                "value": record.value,
            }
            for record in export.records
        ],
    }


def render_etax(export: EtaxExport, fmt: EtaxFormat) -> str:
    """Render an :class:`~ai_books.models.EtaxExport` to the requested concrete format."""
    if fmt is EtaxFormat.CSV:
        return render_etax_csv(export)
    return render_etax_xml(export)


def export_etax(
    financial_statements: FinancialStatements,
    *,
    fmt: EtaxFormat = EtaxFormat.CSV,
    version: str = LATEST_ETAX_VERSION,
) -> str:
    """決算書 → e-Tax 取込データ in one call: build + validate + render to ``fmt``.

    Raises :class:`~ai_books.errors.EtaxValidationError` if the 決算書 maps to invalid output, or
    ``ValueError`` if ``version`` is unknown.
    """
    return render_etax(build_etax_export(financial_statements, version=version), fmt)

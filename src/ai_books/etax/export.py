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
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from importlib.resources import files
from typing import Any

from ai_books.errors import EtaxValidationError
from ai_books.models import EtaxExport, EtaxRecord, EtaxValueKind, FinancialStatements
from ai_books.reports import financial_statements_snapshot

from .spec import (
    LATEST_ETAX_VERSION,
    MISSING,
    EtaxComputedField,
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
    """The concrete file formats e-Tax 取込データ can be rendered to.

    ``CSV`` / ``XML`` are the 補助 (debug / 人間確認) serializations — a flat row/record per cell.
    ``XTX`` is the **real e-Tax 交換ファイル形式** (#79): the 決算書 rendered as the official KOA210
    青色申告決算書(一般用) XML tree, validatable against 国税庁の .xsd. 実申告に渡すのは ``XTX``。
    """

    CSV = "csv"
    XML = "xml"
    XTX = "xtx"


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
    #: section_code → its routed total (Σ emitted 金額); fed to later EtaxComputedField items.
    section_totals: dict[str, Decimal] = {}

    for item in spec.items:
        if isinstance(item, EtaxScalarField):
            _emit_scalar(item, snapshot, records, problems)
        elif isinstance(item, EtaxComputedField):
            _emit_computed(item, snapshot, section_totals, records, problems)
        elif isinstance(item, EtaxSection):
            for row_index, row in enumerate(resolve_list(snapshot, item.source), start=1):
                _emit_section_row(item.form, item.fields, row, row_index, records, problems)
        else:  # EtaxFixedSection
            _emit_fixed_section(item, snapshot, records, problems, section_totals)

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


def _emit_computed(
    field: EtaxComputedField,
    snapshot: dict[str, Any],
    section_totals: dict[str, Decimal],
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> None:
    """Emit a computed scalar: a base 金額 ± earlier sections' routed totals (#83 営業外橋渡し).

    The base is read from ``base_source`` like a scalar 金額; each ``add_sections`` / ``sub_sections``
    ``section_code`` contributes the total an earlier :class:`EtaxFixedSection` routed (0 if absent).
    """
    raw = resolve_scalar(snapshot, field.base_source)
    if raw is MISSING or raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if field.required:
            problems.append(
                {
                    "item_code": field.item_code,
                    "row": "",
                    "message": "required value is missing or empty",
                }
            )
        return
    try:
        amount = Decimal(str(raw).strip())
    except InvalidOperation:
        problems.append(
            {
                "item_code": field.item_code,
                "row": "",
                "message": f"invalid amount {raw!r} (not a number)",
            }
        )
        return
    for code in field.add_sections:
        amount += section_totals.get(code, Decimal(0))
    for code in field.sub_sections:
        amount -= section_totals.get(code, Decimal(0))
    rendered, problem = _validate_value(
        amount, field.kind, required=field.required, max_int_digits=field.max_int_digits
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
    section_totals: dict[str, Decimal] | None = None,
) -> None:
    """Route a 固定勘定科目行 section: lines → fixed 項目コード (summed), spillover → 追加科目枠.

    Each line is matched to the :class:`~ai_books.etax.spec.EtaxFixedRow` whose ``account_codes``
    contain the line's ``code`` (amounts for the same target row are summed, with the row's
    ``sign``). A line whose ``code`` is malformed is rejected (不正コード検出); a line matching no
    fixed row spills into the 追加科目枠 — ``accumulate`` mode sums them into one ``overflow_code``,
    ``slots`` mode fills ``overflow_max`` repeating slots and **errors on overflow** (未分類検出),
    ``drop`` mode discards them silently (#83 営業外橋渡し). The section's routed total (Σ fixed-row +
    追加科目枠 金額) is recorded into ``section_totals[section_code]`` for later EtaxComputedField items.
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

    overflow_total = _emit_overflow(section, overflow, records, problems)
    if section_totals is not None:
        section_totals[section.section_code] = sum(sums.values(), Decimal(0)) + overflow_total


def _emit_overflow(
    section: EtaxFixedSection,
    overflow: list[dict[str, Any]],
    records: list[EtaxRecord],
    problems: list[dict[str, str]],
) -> Decimal:
    """Emit the section's 追加科目枠 and return the total 金額 it emitted (0 if none / dropped).

    ``accumulate`` sums into one cell, ``slots`` fills rep-limited slots, ``drop`` discards 未分類
    silently (#83 — 帳簿上は分類済みだが当該様式に枠が無い 科目)。``drop`` 以外で 居場所が無い 科目は
    未分類エラー (fail-loud, AC #24)。
    """
    # drop モード: 様式に居場所の無い 科目 を意図的に捨てる (橋渡し用)。total には寄与しない。
    if section.overflow_mode == "drop":
        return Decimal(0)

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
        return Decimal(0)

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
        return total

    # slots mode — each 未分類科目 takes the next 追加科目枠 slot; overflow ⇒ 未分類エラー.
    slots_total = Decimal(0)
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
        label = name if isinstance(name, str) and name else section.overflow_label
        # 科目名 タグ (e.g. AMF00060 beside AMF00360) so the .xtx 追加科目 carries its 名称 (#79).
        if section.overflow_name_code is not None:
            records.append(
                EtaxRecord(
                    form=section.form,
                    item_code=section.overflow_name_code,
                    label="追加科目名",
                    kind=EtaxValueKind.TEXT,
                    value=label,
                    row=slot,
                    account_code=code if isinstance(code, str) else None,
                )
            )
        _emit_fixed_value(
            section,
            section.overflow_code,
            label,
            amount,
            records,
            problems,
            row=slot,
            account_code=code if isinstance(code, str) else None,
        )
        slots_total += amount
    return slots_total


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


# ── EtaxExport → .xtx (実 e-Tax 交換ファイル形式, KOA210) ───────────────────────────
#
# The .xtx is the official e-Tax 申告データ XML — a *nested* element tree (pages → groups → leaves),
# not a flat record list. Each 項目コード sits at an exact spot in the 様式; emit it out of order or
# in the wrong namespace and 国税庁の .xsd rejects the file. The nesting/order/repeat are read from
# the committed, XSD-derived layout (``koa210_layout.json``, #76/#79) rather than hard-coded, so a
# 様式 update is a layout regeneration — not a code change. .xtx 形式妥当性は #79 の XSD 検証で機械保証。


@lru_cache(maxsize=1)
def koa210_layout() -> dict[str, Any]:
    """The XSD-derived KOA210 element-tree layout (pages → groups → leaves), loaded once.

    Shipped as package data (``ai_books/etax/koa210_layout.json``), generated from the official
    ``KOA210-011.xsd`` by ``scripts/etax/build_koa210_layout.py``. Each node is a leaf
    (``{"tag", "amount"}``, no children) or a group (``{"tag", "children", "repeat"?}``).
    """
    text = (files("ai_books.etax") / "koa210_layout.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


#: Software identity stamped onto the .xtx root's required ``gen:FormAttribute`` (softNM/sakuseiNM).
_XTX_SOFTWARE_NAME = "ai-books"


def _layout_codes(nodes: list[dict[str, Any]], out: set[str]) -> None:
    """Collect every 項目コード (leaf *and* group tag) appearing in the layout into ``out``."""
    for node in nodes:
        out.add(node["tag"])
        if "children" in node:
            _layout_codes(node["children"], out)


def _descendant_leaf_codes(node: dict[str, Any]) -> set[str]:
    """Every leaf 項目コード beneath ``node`` — the codes that can fill its repeating occurrences."""
    codes: set[str] = set()
    if "children" in node:
        for child in node["children"]:
            if "children" in child:
                codes |= _descendant_leaf_codes(child)
            else:
                codes.add(child["tag"])
    return codes


def _emit_layout_children(
    children: list[dict[str, Any]],
    scalar: dict[str, str],
    repeating: dict[str, dict[int, str]],
    row: int | None,
    ns: str,
) -> list[ET.Element]:
    """Build the XML children for one layout level, in XSD sequence order, skipping empties.

    A **leaf** is emitted only when it has a value (scalar 項目 by ``row=None``; a 繰返し cell by its
    occurrence ``row``) — every 項目 is ``minOccurs=0``, so absent ones are simply omitted. A
    **plain group** is emitted only when it has emitted descendants (so empty 様式区分 don't appear).
    A **repeating group** emits one occurrence per ``row`` present among its descendant leaves.
    """
    out: list[ET.Element] = []
    for node in children:
        tag = node["tag"]
        if "children" not in node:  # leaf
            value = scalar.get(tag) if row is None else repeating.get(tag, {}).get(row)
            if value is not None:
                element = ET.Element(f"{{{ns}}}{tag}")
                element.text = value
                out.append(element)
            continue
        if node.get("repeat"):  # repeating 繰返しブロック
            occupied = sorted(
                {r for code in _descendant_leaf_codes(node) for r in repeating.get(code, {})}
            )
            for occurrence_row in occupied:
                inner = _emit_layout_children(
                    node["children"], scalar, repeating, occurrence_row, ns
                )
                if inner:
                    wrapper = ET.Element(f"{{{ns}}}{tag}")
                    wrapper.extend(inner)
                    out.append(wrapper)
            continue
        inner = _emit_layout_children(node["children"], scalar, repeating, row, ns)  # plain group
        if inner:
            wrapper = ET.Element(f"{{{ns}}}{tag}")
            wrapper.extend(inner)
            out.append(wrapper)
    return out


def render_etax_xtx(export: EtaxExport) -> str:
    """Render the e-Tax 取込データ as the real ``.xtx`` (official KOA210 青色申告決算書 XML, #79).

    Places every record's 値 at its 項目コード's spot in the KOA210 element tree (read from the
    XSD-derived :func:`koa210_layout`), in 様式 (XSD sequence) order, under the
    ``http://xml.e-tax.nta.go.jp/XSD/shotoku`` 名前空間. The root ``<KOA210>`` carries the required
    ``VR`` (様式バージョン) and ``gen:FormAttribute`` (softNM / sakuseiNM / sakuseiDay) so the file is
    schema-valid; ``sakuseiDay`` is the 期末日 (deterministic, not "今日" — golden 安定のため).

    Only the real 様式 (``version="2025"`` → KOA210) renders to .xtx: a record whose 項目コード is not
    in the KOA210 layout (e.g. the synthetic 様式's ``PL010``) raises ``ValueError`` rather than being
    silently dropped (fail loud). 出力は決定的 (records → 固定木 → 2-space indent)。
    """
    layout = koa210_layout()
    namespace = layout["namespace"]

    known: set[str] = set()
    for page in layout["pages"]:
        _layout_codes(page["children"], known)
    unknown = sorted({r.item_code for r in export.records} - known)
    if unknown:
        raise ValueError(
            "cannot render .xtx: 項目コード not in the KOA210 layout "
            f"({', '.join(unknown)}); .xtx requires the 2025 KOA210 様式 "
            "(synthetic/旧様式 outputs CSV/XML only)"
        )

    scalar: dict[str, str] = {}
    repeating: dict[str, dict[int, str]] = {}
    for record in export.records:
        if record.row is None:
            scalar[record.item_code] = record.value
        else:
            repeating.setdefault(record.item_code, {})[record.row] = record.value

    ET.register_namespace("", namespace)
    root = ET.Element(
        f"{{{namespace}}}KOA210",
        {
            "VR": layout["version"],
            "softNM": _XTX_SOFTWARE_NAME,
            "sakuseiNM": _XTX_SOFTWARE_NAME,
            "sakuseiDay": export.end_date.isoformat(),
        },
    )
    for page in layout["pages"]:
        page_children = _emit_layout_children(page["children"], scalar, repeating, None, namespace)
        if page_children:
            page_element = ET.SubElement(root, f"{{{namespace}}}{page['tag']}")
            page_element.extend(page_children)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def render_etax(export: EtaxExport, fmt: EtaxFormat) -> str:
    """Render an :class:`~ai_books.models.EtaxExport` to the requested concrete format."""
    if fmt is EtaxFormat.CSV:
        return render_etax_csv(export)
    if fmt is EtaxFormat.XTX:
        return render_etax_xtx(export)
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

#!/usr/bin/env python3
"""Regenerate ``docs/etax/field_catalog.json`` from the official e-Tax 帳票フィールド仕様書.

Spike #76 artifact. Reads the 帳票フィールド仕様書 (所得-申告) workbooks extracted from the
国税庁 spec package ``e-tax09.CAB`` (see :mod:`fetch_etax_spec`) and emits a machine-readable
field catalog for the 青色申告決算書 帳票 (一般用 / 不動産所得用 / 農業所得用) plus the
製造原価の計算 sub-section that lives inside the 一般用 form.

The raw workbooks are **not** redistributed in this repo (国税庁 著作物; see ``docs/etax/README.md``);
run ``scripts/etax/fetch_etax_spec.py`` first to populate the local extraction directory, then run
this script to regenerate the committed JSON. No third-party dependencies — the ``.xlsx`` (an OPC
zip of XML) is parsed directly.

Usage::

    python scripts/etax/build_field_catalog.py --spec-dir <extracted e-tax09 dir> \
        --out docs/etax/field_catalog.json
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# 帳票フィールド仕様書 column layout (0-based), confirmed against KOA210/220/240 headers.
COL_SEQ = 0  # 項番
COL_KIND = 1  # 入力型 (文字 / 区分 / 数値 / 金額 …)
COL_GROUP = 3  # 項目グループ名
COL_NAME = 4  # 項目名
COL_REPEAT = 5  # 繰返し回数
COL_FORMAT = 6  # 書式
COL_INPUT_CHECK = 7  # 入力チェック (○ = 入力必須)
COL_RANGE = 9  # 値の範囲
COL_NOTE = 11  # 計算・備考
COL_TAG = 12  # ＸＭＬタグ (= 項目コード)

# Which 帳票 to catalog: 様式ID -> (workbook version file, human label, optional sub-section tag).
FORMS = [
    ("KOA210", "Ver11x", "青色申告決算書(一般用)"),
    ("KOA220", "Ver8x", "青色申告決算書(不動産所得用)"),
    ("KOA240", "Ver8x", "青色申告決算書(農業所得用)"),
]

WORKBOOK_GLOB = "帳票フィールド仕様書(所得-申告){version}.xlsx"


def _col_index(ref: str) -> int:
    """Column index (0-based) from an A1-style cell reference (``"AB12"`` -> 27)."""
    letters = re.match(r"[A-Z]+", ref).group(0)  # type: ignore[union-attr]
    idx = 0
    for char in letters:
        idx = idx * 26 + (ord(char) - 64)
    return idx - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in root.findall(f"{NS}si")]


def _sheet_targets(zf: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
    targets: dict[str, str] = {}
    for sheet in workbook.find(f"{NS}sheets"):  # type: ignore[union-attr]
        target = rid_to_target.get(sheet.get(f"{REL_NS}id"), "")
        if not target.startswith("/"):
            target = "xl/" + target.lstrip("./")
        targets[sheet.get("name")] = target
    return targets


def _read_sheet(zf: zipfile.ZipFile, target: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(target))
    sheet_data = root.find(f"{NS}sheetData")
    rows: list[list[str]] = []
    if sheet_data is None:
        return rows
    for row in sheet_data.findall(f"{NS}row"):
        cells: dict[int, str] = {}
        width = 0
        for cell in row.findall(f"{NS}c"):
            idx = _col_index(cell.get("r"))  # type: ignore[arg-type]
            value_el = cell.find(f"{NS}v")
            if cell.get("t") == "s" and value_el is not None:
                value = shared[int(value_el.text)]  # type: ignore[arg-type]
            elif cell.get("t") == "inlineStr":
                inline = cell.find(f"{NS}is")
                value = (
                    "".join(t.text or "" for t in inline.iter(f"{NS}t"))
                    if inline is not None
                    else ""
                )
            else:
                value = value_el.text if value_el is not None else ""
            cells[idx] = (value or "").strip()
            width = max(width, idx)
        rows.append([cells.get(i, "") for i in range(width + 1)])
    return rows


def _int_digits(fmt: str) -> int | None:
    """Integer-digit count implied by a 書式 mask (``"Z,ZZZ,ZZZ"`` -> 7); ``None`` if not numeric."""
    digits = re.sub(r"[^Z9]", "", fmt or "")
    return len(digits) or None


def _extract_form(rows: list[list[str]], form_id: str, label: str) -> dict:
    fields: list[dict] = []
    current_group = ""
    for row in rows:
        if len(row) <= COL_TAG:
            continue
        seq = row[COL_SEQ].strip()
        tag = row[COL_TAG].strip()
        if not seq.isdigit() or not tag:
            continue
        group = row[COL_GROUP].strip()
        if group:
            current_group = group
        fmt = row[COL_FORMAT].strip()
        repeat = row[COL_REPEAT].strip()
        fields.append(
            {
                "seq": int(seq),
                "item_code": tag,
                "group": current_group,
                "name": row[COL_NAME].strip(),
                "kind": row[COL_KIND].strip(),
                "format": fmt,
                "int_digits": _int_digits(fmt),
                "repeat": int(repeat) if repeat.isdigit() else (repeat or None),
                "input_required": row[COL_INPUT_CHECK].strip() == "○",
                "value_range": row[COL_RANGE].strip() or None,
                "note": row[COL_NOTE].strip() or None,
            }
        )
    return {"form_id": form_id, "label": label, "field_count": len(fields), "fields": fields}


def build(spec_dir: Path) -> dict:
    forms: list[dict] = []
    for form_id, version, label in FORMS:
        workbook = spec_dir / WORKBOOK_GLOB.format(version=version)
        if not workbook.exists():
            raise SystemExit(f"workbook not found: {workbook}")
        with zipfile.ZipFile(workbook) as zf:
            shared = _shared_strings(zf)
            targets = _sheet_targets(zf)
            if form_id not in targets:
                raise SystemExit(f"sheet {form_id} not in {workbook.name}")
            rows = _read_sheet(zf, targets[form_id], shared)
        form = _extract_form(rows, form_id, label)
        form["source_workbook"] = workbook.name
        forms.append(form)
    return {
        "spec": "e-Tax 所得税関係 — 帳票フィールド仕様書 (所得-申告)",
        "source": "https://www.e-tax.nta.go.jp/shiyo/shiyo3.htm (e-tax09.CAB)",
        "generated_by": "scripts/etax/build_field_catalog.py",
        "note": (
            "項目コード = ＸＭＬタグ. int_digits は 書式マスク(Z/9)の桁数から導出. "
            "input_required は 帳票フィールド仕様書 の入力チェック(○)列。スキーマ上の "
            "minOccurs とは別物(多くの金額項目は minOccurs=0)。"
        ),
        "forms": forms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-dir", required=True, type=Path, help="extracted e-tax09 dir")
    parser.add_argument("--out", required=True, type=Path, help="output JSON path")
    args = parser.parse_args()
    catalog = build(args.spec_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(f["field_count"] for f in catalog["forms"])
    print(f"wrote {args.out} — {len(catalog['forms'])} forms, {total} fields")


if __name__ == "__main__":
    main()

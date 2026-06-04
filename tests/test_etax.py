"""Pure (no-DB) tests for the e-Tax 取込データ export (Issue #24).

Exercise the 決算書 → e-Tax mapping, schema validation, and CSV/XML rendering over the synthetic
year and small mutations of it. These run everywhere (no Postgres); the DB round-trip that checks
the *data* against golden lives in ``test_seed_fy_db.py``. Here we pin the *output shapes* and the
validation behaviour (必須項目欠落・不正コード・端数・桁あふれをエラーにする).
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from ai_books.errors import EtaxValidationError
from ai_books.etax import (
    LATEST_ETAX_VERSION,
    EtaxFormat,
    build_etax_export,
    etax_export_snapshot,
    export_etax,
    get_format_spec,
    parse_etax_format,
    render_etax_csv,
    render_etax_xml,
)
from ai_books.etax.spec import MISSING, resolve_list, resolve_scalar
from tests.fixtures.seed_fy import etax_export_from_dataset, load_golden
from tests.fixtures.seed_fy.reports import financial_statements_from_dataset

_CSV_HEADER = ["面", "項目コード", "項目名", "行", "勘定科目コード", "値"]


# --- mapping / build ------------------------------------------------------------


def test_build_export_carries_version_and_orders_records() -> None:
    export = etax_export_from_dataset()
    assert export.format_version == "2025"
    assert export.form_id == "青色申告決算書(一般用)"
    assert export.fiscal_year == "FY2025"
    # The 段階利益 scalars lead (PL 面 first, in spec order).
    leading = [(r.item_code, r.value) for r in export.records[:4]]
    assert leading == [
        ("PL010", "1650000"),  # 売上(収入)金額
        ("PL020", "1490000"),  # 売上原価
        ("PL030", "160000"),  # 売上総利益
        ("PL040", "720000"),  # 経費
    ]


def test_amounts_are_whole_yen_no_decimal_point() -> None:
    # e-Tax 取込は円単位 — every 金額 record renders as an integer string (no '.00', no float).
    export = etax_export_from_dataset()
    amounts = [r.value for r in export.records if r.kind.value == "amount"]
    assert amounts, "expected some amount records"
    assert all("." not in value for value in amounts)
    # A loss figure keeps its sign.
    net_income = next(r for r in export.records if r.item_code == "PL090")
    assert net_income.value == "-580500"


def test_section_rows_tile_and_carry_account_codes() -> None:
    export = etax_export_from_dataset()
    months = [r for r in export.records if r.item_code == "MN010"]
    assert [r.value for r in months] == [f"2025-{m:02d}" for m in range(1, 13)]  # 12 行
    # 資産の部 内訳 flattens 流動資産 + 固定資産 (5 + 2 = 7 lines), each carrying its 勘定科目コード.
    asset_codes = [r.account_code for r in export.records if r.item_code == "BS010"]
    assert asset_codes == ["1110", "1141", "1160", "1180", "1290", "1530", "1550"]
    cash_balance = next(
        r for r in export.records if r.item_code == "BS012" and r.account_code == "1110"
    )
    assert cash_balance.value == "300000"
    assert cash_balance.row == 1


# --- CSV / XML rendering --------------------------------------------------------


def test_csv_has_header_and_one_row_per_record() -> None:
    export = etax_export_from_dataset()
    rows = list(csv.reader(io.StringIO(render_etax_csv(export))))
    assert rows[0] == _CSV_HEADER
    assert len(rows) == len(export.records) + 1  # + header
    # A scalar 項目 leaves 行 / 勘定科目コード blank; a 内訳 row fills both.
    pl010 = next(r for r in rows if r[1] == "PL010")
    assert pl010[3] == ""  # 行
    assert pl010[4] == ""  # 勘定科目コード
    assert pl010[5] == "1650000"  # 値
    bs012 = next(r for r in rows if r[1] == "BS012" and r[4] == "1110")
    assert bs012[3] == "1"  # 行
    assert bs012[5] == "300000"  # 値


def test_xml_is_wellformed_and_round_trips_record_count() -> None:
    export = etax_export_from_dataset()
    xml = render_etax_xml(export)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    assert root.tag == "etaxExport"
    assert root.attrib["version"] == "2025"
    assert root.attrib["fiscalYear"] == "FY2025"
    records = root.findall("record")
    assert len(records) == len(export.records)
    pl010 = next(e for e in records if e.attrib["itemCode"] == "PL010")
    assert pl010.text == "1650000"
    assert "row" not in pl010.attrib  # scalar carries no row
    bs010 = next(
        e
        for e in records
        if e.attrib["itemCode"] == "BS010" and e.attrib.get("accountCode") == "1110"
    )
    assert bs010.attrib["row"] == "1"


def test_export_etax_dispatches_on_format() -> None:
    fs = financial_statements_from_dataset()
    assert export_etax(fs, fmt=EtaxFormat.CSV).splitlines()[0] == ",".join(_CSV_HEADER)
    assert export_etax(fs, fmt=EtaxFormat.XML).startswith("<?xml")
    # Default format is CSV.
    assert export_etax(fs) == export_etax(fs, fmt=EtaxFormat.CSV)


def test_render_is_deterministic() -> None:
    fs = financial_statements_from_dataset()
    assert export_etax(fs, fmt=EtaxFormat.CSV) == export_etax(fs, fmt=EtaxFormat.CSV)
    assert export_etax(fs, fmt=EtaxFormat.XML) == export_etax(fs, fmt=EtaxFormat.XML)


# --- schema validation ----------------------------------------------------------


def test_invalid_account_code_is_rejected() -> None:
    # AC (#24): 不正コードを検出してエラーにする.
    fs = financial_statements_from_dataset()
    fs.balance_sheet.assets[0].lines[0].code = "ABC"
    with pytest.raises(EtaxValidationError) as excinfo:
        build_etax_export(fs)
    problems = excinfo.value.problems
    assert any("勘定科目コード" in p["message"] for p in problems)
    # The payload is machine-readable and points at the offending 行.
    assert excinfo.value.to_dict()["error"] == "etax_validation_error"
    assert any(p["row"] == "1" for p in problems)


def test_fractional_yen_is_rejected() -> None:
    # AC (#24): 桁・金額の妥当性 — e-Tax は整数円。端数のある金額はエラー。
    fs = financial_statements_from_dataset()
    fs.monthly.rows[0].sales = Decimal("100.50")
    with pytest.raises(EtaxValidationError) as excinfo:
        build_etax_export(fs)
    assert any("whole yen" in p["message"] for p in excinfo.value.problems)


def test_all_problems_are_collected_not_just_the_first() -> None:
    fs = financial_statements_from_dataset()
    fs.balance_sheet.assets[0].lines[0].code = "ABC"
    fs.monthly.rows[0].sales = Decimal("100.50")
    with pytest.raises(EtaxValidationError) as excinfo:
        build_etax_export(fs)
    messages = [p["message"] for p in excinfo.value.problems]
    assert any("勘定科目コード" in m for m in messages)
    assert any("whole yen" in m for m in messages)


def test_unknown_version_raises() -> None:
    with pytest.raises(ValueError, match="unknown e-Tax format version"):
        build_etax_export(financial_statements_from_dataset(), version="1999")
    with pytest.raises(ValueError, match="unknown e-Tax format version"):
        get_format_spec("1999")


def test_parse_format_rejects_unknown() -> None:
    assert parse_etax_format("csv") is EtaxFormat.CSV
    assert parse_etax_format("xml") is EtaxFormat.XML
    with pytest.raises(ValueError, match="format must be one of"):
        parse_etax_format("pdf")


# --- golden (offline guard; the DB round-trip lives in test_seed_fy_db.py) ------


def test_snapshot_matches_committed_golden() -> None:
    # The offline export equals the committed golden — guards the file against accidental edits
    # and pins the LATEST version's mapping. (出力が #17 の golden と一致.)
    snapshot = etax_export_snapshot(etax_export_from_dataset())
    assert snapshot == load_golden("etax_export")
    assert snapshot["format_version"] == LATEST_ETAX_VERSION


# --- path resolver --------------------------------------------------------------


def test_path_resolver_scalar_and_flatten() -> None:
    snapshot = {
        "a": {"b": "x"},
        "list": [{"lines": [{"v": 1}, {"v": 2}]}, {"lines": [{"v": 3}]}],
    }
    assert resolve_scalar(snapshot, "a.b") == "x"
    assert resolve_scalar(snapshot, "a.missing") is MISSING
    # '[]' flattens nested lists one level.
    flattened = resolve_list(snapshot, "list[].lines")
    assert flattened == [{"v": 1}, {"v": 2}, {"v": 3}]
    # A missing list path resolves to an empty list (zero rows, not an error).
    assert resolve_list(snapshot, "nope[].lines") == []

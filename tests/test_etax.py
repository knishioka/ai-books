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
    # KOA210(一般用) 損益計算書 leads: 売上 → 売上原価 (期首/期末/仕入) → 差引原価 → 差引金額1.
    leading = [(r.item_code, r.value) for r in export.records[:6]]
    assert leading == [
        ("AMF00100", "1650000"),  # 売上(収入)金額
        ("AMF00120", "300000"),  # 期首商品(製品)棚卸高
        ("AMF00150", "350000"),  # 期末商品(製品)棚卸高 (snapshot は contra; 様式は正値)
        ("AMF00130", "1540000"),  # 仕入金額(製品製造原価) = 商品仕入 + 製造原価内訳
        ("AMF00160", "1490000"),  # 差引原価 = 期首 + 仕入 - 期末
        ("AMF00170", "160000"),  # 差引金額１
    ]


def test_cost_of_goods_fixed_calc_balances() -> None:
    # 実 様式は 期首 + 仕入 - 期末 = 差引原価 の固定計算 (差引原価 = COGS subtotal).
    export = etax_export_from_dataset()
    by_code = {r.item_code: int(r.value) for r in export.records if r.kind.value == "amount"}
    assert by_code["AMF00120"] + by_code["AMF00130"] - by_code["AMF00150"] == by_code["AMF00160"]


def test_amounts_are_whole_yen_no_decimal_point() -> None:
    # e-Tax 取込は円単位 — every 金額 record renders as an integer string (no '.00', no float).
    export = etax_export_from_dataset()
    amounts = [r.value for r in export.records if r.kind.value == "amount"]
    assert amounts, "expected some amount records"
    assert all("." not in value for value in amounts)
    # A loss figure keeps its sign (青色申告特別控除前の所得金額).
    net_income = next(r for r in export.records if r.item_code == "AMF00500")
    assert net_income.value == "-580500"


def test_monthly_rows_map_to_fixed_per_month_codes() -> None:
    # 実 様式は月ごとに固定タグ (1月 AMF00600 … 12月 AMF00930); not a repeating block.
    export = etax_export_from_dataset()
    sales_codes = [f"AMF{600 + (m - 1) * 30:05d}" for m in range(1, 13)]
    months = {r.item_code: r.value for r in export.records if r.item_code in sales_codes}
    assert len(months) == 12
    assert months["AMF00840"] == "880000"  # 9月 売上
    total = next(r for r in export.records if r.item_code == "AMF00980")  # 月別売上 計
    assert total.value == "1650000"


def test_fixed_account_rows_carry_their_account_code() -> None:
    # 資産の部 固定行: 各 勘定科目コード が単独で寄与した行は account_code を辿れる.
    export = etax_export_from_dataset()
    asset_codes = [
        r.account_code for r in export.records if r.form == "BS" and r.account_code is not None
    ]
    # 現金(1110)/普通預金(1141)/売掛金(1160)/商品(1180)/機械装置(1530)/工具器具備品(1550)/事業主貸(1290)
    assert asset_codes[:7] == ["1110", "1141", "1160", "1180", "1530", "1550", "1290"]
    cash = next(r for r in export.records if r.item_code == "AMG00260" and r.account_code == "1110")
    assert cash.value == "300000"
    assert cash.row is None  # 固定行 は スカラ扱い (繰返し row ではない)


def test_balance_sheet_liability_equity_side_balances() -> None:
    # 負債・資本の部 固定行 + 所得(AMG00750) の合計 = 部 合計(AMG00760) = 資産合計(AMG00440).
    export = etax_export_from_dataset()
    by_code = {r.item_code: int(r.value) for r in export.records if r.kind.value == "amount"}
    assert by_code["AMG00440"] == by_code["AMG00760"]  # 貸借一致
    assert by_code["AMG00660"] == 600000  # 借入金 = 短期 + 長期 を合算


def test_unclassified_account_overflows_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC: 未分類項目を検出. 固定行にも追加科目枠にも収まらない 科目は fail-fast.
    from ai_books.reports import financial_statements_snapshot

    fs = financial_statements_from_dataset()
    snapshot = financial_statements_snapshot(fs)
    # 経費に 固定行に無いコードを 追加科目枠(6) を超える数だけ注入する.
    extra = [
        {"code": f"79{i:02d}", "name": f"未分類経費{i}", "amount": "1000", "category": "x"}
        for i in range(7)
    ]
    snapshot["profit_and_loss"]["selling_admin_expenses"]["lines"].extend(extra)
    monkeypatch.setattr("ai_books.etax.export.financial_statements_snapshot", lambda _: snapshot)
    with pytest.raises(EtaxValidationError) as excinfo:
        build_etax_export(fs)
    assert any("追加科目枠" in p["message"] for p in excinfo.value.problems)


# --- 営業外 マッピング方針 (#83) ------------------------------------------------


def test_homed_non_operating_expense_bridges_to_expense_cell() -> None:
    # 利子割引料 は帳簿上 営業外費用(8210) だが KOA210 では 経費 AMF00330。橋渡しして居場所に置く。
    export = etax_export_from_dataset()
    interest = next(r for r in export.records if r.item_code == "AMF00330")
    assert interest.form == "PL"
    assert interest.label == "利子割引料"
    assert interest.account_code == "8210"  # 帳簿の 営業外費用 コードを辿れる
    assert interest.value == "21000"


def test_expense_total_reconciles_with_homed_non_operating() -> None:
    # 橋渡しした 利子割引料 を 経費計(AMF00380)/差引金額２(AMF00390) に織り込み、様式の内訳整合を保つ。
    export = etax_export_from_dataset()
    by_code = {r.item_code: int(r.value) for r in export.records if r.kind.value == "amount"}
    expense_rows = sum(
        int(r.value)
        for r in export.records
        # 経費 固定行 + 橋渡しした 利子割引料 (追加科目枠/計/差引 は除く)
        if r.form == "PL"
        and "AMF00190" <= r.item_code <= "AMF00370"
        and r.item_code not in {"AMF00360", "AMF00380"}
    )
    assert expense_rows == by_code["AMF00380"]  # 経費行合計 = 経費計
    assert (
        by_code["AMF00170"] - by_code["AMF00380"] == by_code["AMF00390"]
    )  # 差引1 - 経費計 = 差引2
    # 所得(AMF00500) は net_income のまま — 非居場所 営業外 (受取利息) の net が 差引2 との残差。
    assert by_code["AMF00500"] - by_code["AMF00390"] == 500  # 受取利息(8110)


def test_non_operating_without_form_home_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # 様式に居場所の無い 営業外 (雑損失 8220) は 意図的に drop — 未分類エラーにも 追加科目枠にもしない。
    from ai_books.reports import financial_statements_snapshot

    fs = financial_statements_from_dataset()
    snapshot = financial_statements_snapshot(fs)
    snapshot["profit_and_loss"]["non_operating_expenses"]["lines"].append(
        {"code": "8220", "name": "雑損失", "category": "non_operating_expenses", "amount": "9999"}
    )
    monkeypatch.setattr("ai_books.etax.export.financial_statements_snapshot", lambda _: snapshot)
    export = build_etax_export(fs)  # raises if 雑損失 were treated as 未分類
    codes = {r.account_code for r in export.records}
    assert "8220" not in codes  # drop されて出力に現れない
    by_code = {r.item_code: int(r.value) for r in export.records if r.kind.value == "amount"}
    assert by_code["AMF00380"] == 741000  # 経費計 は 利子割引料 のみ織り込み (雑損失 は寄与しない)


def test_computed_field_handles_empty_non_operating(monkeypatch: pytest.MonkeyPatch) -> None:
    # 営業外費用 が空でも 経費計/差引金額２ は base 値そのまま (section total = 0)。
    from ai_books.reports import financial_statements_snapshot

    fs = financial_statements_from_dataset()
    snapshot = financial_statements_snapshot(fs)
    snapshot["profit_and_loss"]["non_operating_expenses"]["lines"] = []
    monkeypatch.setattr("ai_books.etax.export.financial_statements_snapshot", lambda _: snapshot)
    export = build_etax_export(fs)
    by_code = {r.item_code: int(r.value) for r in export.records if r.kind.value == "amount"}
    assert by_code["AMF00380"] == 720000  # 販管費小計のみ (利子割引料 抜き)
    assert by_code["AMF00390"] == -560000  # 営業利益そのまま
    assert not any(r.item_code == "AMF00330" for r in export.records)  # 利子割引料 行は出ない


# --- CSV / XML rendering --------------------------------------------------------


def test_csv_has_header_and_one_row_per_record() -> None:
    export = etax_export_from_dataset()
    rows = list(csv.reader(io.StringIO(render_etax_csv(export))))
    assert rows[0] == _CSV_HEADER
    assert len(rows) == len(export.records) + 1  # + header
    # A scalar 項目 leaves 行 / 勘定科目コード blank; a 固定勘定科目行 fills 勘定科目コード.
    sales = next(r for r in rows if r[1] == "AMF00100")
    assert sales[3] == ""  # 行
    assert sales[4] == ""  # 勘定科目コード
    assert sales[5] == "1650000"  # 値
    cash = next(r for r in rows if r[1] == "AMG00260" and r[4] == "1110")
    assert cash[3] == ""  # 固定行 は row 無し
    assert cash[5] == "300000"  # 値


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
    sales = next(e for e in records if e.attrib["itemCode"] == "AMF00100")
    assert sales.text == "1650000"
    assert "row" not in sales.attrib  # scalar carries no row
    cash = next(
        e
        for e in records
        if e.attrib["itemCode"] == "AMG00260" and e.attrib.get("accountCode") == "1110"
    )
    assert "row" not in cash.attrib  # 固定勘定科目行 carries 勘定科目コード but no 繰返し row


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
    # The payload is machine-readable.
    assert excinfo.value.to_dict()["error"] == "etax_validation_error"


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


def test_synthetic_spec_is_isolated_off_the_year_axis() -> None:
    # 合成様式は撤去せず "synthetic" キーで年度軸外に隔離 (#78); 実様式 "2025" と取り違えない.
    assert LATEST_ETAX_VERSION == "2025"
    synthetic = get_format_spec("synthetic")
    assert synthetic.form_id == "青色申告決算書(一般用・合成)"
    # data-driven 機構は実様式と独立に回り続ける: 合成様式でも build できる.
    export = build_etax_export(financial_statements_from_dataset(), version="synthetic")
    assert export.format_version == "synthetic"
    assert any(r.item_code == "PL010" for r in export.records)  # 合成様式の項目コード


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

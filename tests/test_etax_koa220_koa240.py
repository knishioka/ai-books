"""Pure (no-DB) tests for the KOA220(不動産所得用) / KOA240(農業所得用) e-Tax specs (Issue #126).

stage 4 registers the 収入側 :class:`~ai_books.etax.spec.EtaxFormatSpec` for both 様式 and wires the
end-to-end ``.xtx``. These tests pin the build (収入側 snapshot → records, totals reconcile, the right
様式 spec/version), the rendered ``.xtx`` against committed golden, and — gated on the fetched .xsd —
the real-data ``.xtx`` passing the official KOA220-008 / KOA240-008 schema (previously only a minimal
.xtx was validated). They run everywhere; the .xsd gate skips offline exactly as for KOA210.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from ai_books.etax import (
    build_agricultural_etax_export,
    build_real_estate_etax_export,
    etax_export_snapshot,
    get_format_spec,
    render_etax_xtx,
)
from ai_books.models import EtaxExport
from tests.etax_xsd import skip_reason, validate_xtx, xsd_available
from tests.fixtures.seed_fy import (
    agricultural_income_from_dataset,
    load_golden,
    real_estate_income_from_dataset,
)

_NS = "http://xml.e-tax.nta.go.jp/XSD/shotoku"
_requires_xsd = pytest.mark.skipif(not xsd_available(), reason=skip_reason())


def _q(tag: str) -> str:
    """Qualify a 項目コード with the shotoku namespace for ElementTree lookups."""
    return f"{{{_NS}}}{tag}"


def _value(export: EtaxExport, item_code: str, row: int | None = None) -> str | None:
    """The 値 of the (item_code, row) record in ``export``, or ``None`` if absent."""
    for record in export.records:
        if record.item_code == item_code and record.row == row:
            return record.value
    return None


# --- spec registration / build -------------------------------------------------


def test_specs_registered_under_form_specific_versions() -> None:
    assert get_format_spec("2025-KOA220").form_id == "青色申告決算書(不動産所得用)"
    assert get_format_spec("2025-KOA240").form_id == "青色申告決算書(農業所得用)"


def test_real_estate_build_carries_version_and_form() -> None:
    export = build_real_estate_etax_export(real_estate_income_from_dataset())
    assert export.format_version == "2025-KOA220"
    assert export.form_id == "青色申告決算書(不動産所得用)"
    assert export.fiscal_year == "FY2025"
    assert export.records  # 収入側が空でない


def test_agricultural_build_carries_version_and_form() -> None:
    export = build_agricultural_etax_export(agricultural_income_from_dataset())
    assert export.format_version == "2025-KOA240"
    assert export.form_id == "青色申告決算書(農業所得用)"
    assert export.fiscal_year == "FY2025"
    assert export.records


def test_rental_income_rows_and_totals_reconcile() -> None:
    # 二物件 (甲 賃貸料年額 1,200,000 / 乙 900,000) が ANF00500 の 1行目・2行目に入り、計 ANF00570 と一致.
    export = build_real_estate_etax_export(real_estate_income_from_dataset())
    assert _value(export, "ANF00500", row=1) == "1200000"
    assert _value(export, "ANF00500", row=2) == "900000"
    assert _value(export, "ANF00570") == "2100000"  # 賃貸料年額 計
    # 礼金 甲 100,000 + 更新料 乙 60,000 → 礼金・権利金・更新料 計 ANF00580.
    assert _value(export, "ANF00580") == "160000"
    # 保証金・敷金 甲 200,000 → 計 ANF00600.
    assert _value(export, "ANF00600") == "200000"


def test_loan_interest_maps_balance_and_interest() -> None:
    # 期末借入金残高 (8,000,000 - 500,000 元金返済) と 本年中の借入金利子 80,000.
    export = build_real_estate_etax_export(real_estate_income_from_dataset())
    assert _value(export, "ANF01300", row=1) == "7500000"
    assert _value(export, "ANF01310", row=1) == "80000"
    assert _value(export, "ANF01320", row=1) == "80000"  # 全額 必要経費算入


def test_farm_products_rows_and_income_summary_reconcile() -> None:
    # 4 作物の販売金額 (800k+300k+400k+500k) = 2,000,000 が農産物 計 APF01070 と page1 販売金額 APF00110 に乗る.
    export = build_agricultural_etax_export(agricultural_income_from_dataset())
    crop_sales = [_value(export, "APF00750", row=row) for row in (1, 2, 3, 4)]
    assert crop_sales == ["800000", "300000", "400000", "500000"]
    assert _value(export, "APF01070") == "2000000"  # 農産物 販売金額 計
    # 区分 (田畑/果樹/特殊施設) が APF00690 に写る.
    assert _value(export, "APF00690", row=1) == "田畑"
    assert _value(export, "APF00690", row=3) == "果樹"
    # 収入金額 summary: 販売金額 (農産物2,000,000 + 畜産1,800,000) + 雑収入 200,000.
    assert _value(export, "APF00130") == "200000"  # 雑収入
    gross = _value(export, "APF00180")  # 収入金額(計)
    assert gross is not None
    assert Decimal(gross) > 0


def test_livestock_and_misc_sections_present() -> None:
    export = build_agricultural_etax_export(agricultural_income_from_dataset())
    # 畜産物 2 行 (肉用牛 1,200,000 / 鶏卵 600,000) と 計 APF01170 = 1,800,000.
    assert _value(export, "APF01140", row=1) == "1200000"
    assert _value(export, "APF01140", row=2) == "600000"
    assert _value(export, "APF01170") == "1800000"
    # 雑収入 2 行 → 合計金額 APF01230 = 200,000.
    assert _value(export, "APF01230") == "200000"


# --- .xtx 様式選択 / 構造 -------------------------------------------------------


def test_real_estate_xtx_selects_koa220_layout() -> None:
    root = ET.fromstring(
        render_etax_xtx(build_real_estate_etax_export(real_estate_income_from_dataset()))
    )
    assert root.tag == _q("KOA220")
    assert root.attrib["VR"] == "8.0"
    # 賃貸 内訳 は ANF00330 > ANF00340 (繰返し) に物件ごと入る.
    blocks = root.findall(f".//{_q('ANF00340')}")
    assert len(blocks) == 2
    assert blocks[0].find(f"{_q('ANF00440')}/{_q('ANF00450')}/{_q('ANF00500')}") is not None


def test_agricultural_xtx_selects_koa240_layout() -> None:
    root = ET.fromstring(
        render_etax_xtx(build_agricultural_etax_export(agricultural_income_from_dataset()))
    )
    assert root.tag == _q("KOA240")
    assert root.attrib["VR"] == "8.0"
    # 農産物 内訳 は繰返しブロック APF00680: 4 作物 → 4 occurrence.
    assert len(root.findall(f".//{_q('APF00680')}")) == 4


# --- golden (offline regression guard) -----------------------------------------


def test_real_estate_export_matches_golden() -> None:
    snapshot = etax_export_snapshot(
        build_real_estate_etax_export(real_estate_income_from_dataset())
    )
    assert snapshot == load_golden("etax_export_koa220")


def test_real_estate_xtx_matches_golden() -> None:
    snapshot = {
        "report": "etax_xtx",
        "form_id": "KOA220",
        "version": "8.0",
        "namespace": _NS,
        "xtx_lines": render_etax_xtx(
            build_real_estate_etax_export(real_estate_income_from_dataset())
        ).splitlines(),
    }
    assert snapshot == load_golden("etax_xtx_koa220")


def test_agricultural_export_matches_golden() -> None:
    snapshot = etax_export_snapshot(
        build_agricultural_etax_export(agricultural_income_from_dataset())
    )
    assert snapshot == load_golden("etax_export_koa240")


def test_agricultural_xtx_matches_golden() -> None:
    snapshot = {
        "report": "etax_xtx",
        "form_id": "KOA240",
        "version": "8.0",
        "namespace": _NS,
        "xtx_lines": render_etax_xtx(
            build_agricultural_etax_export(agricultural_income_from_dataset())
        ).splitlines(),
    }
    assert snapshot == load_golden("etax_xtx_koa240")


# --- 形式妥当性: official XSD (gated on the fetched .xsd) ------------------------


@_requires_xsd
def test_real_estate_xtx_passes_official_xsd() -> None:
    # AC #126: 実データ由来の KOA220 .xtx が 国税庁 KOA220-008.xsd を pass (最小 .xtx だけでなく).
    if not xsd_available("KOA220"):
        pytest.skip("official KOA220 .xsd not fetched")
    errors = validate_xtx(
        render_etax_xtx(build_real_estate_etax_export(real_estate_income_from_dataset()))
    )
    assert errors == [], f"unexpected XSD errors for KOA220: {errors}"


@_requires_xsd
def test_agricultural_xtx_passes_official_xsd() -> None:
    # AC #126: 実データ由来の KOA240 .xtx が 国税庁 KOA240-008.xsd を pass.
    if not xsd_available("KOA240"):
        pytest.skip("official KOA240 .xsd not fetched")
    errors = validate_xtx(
        render_etax_xtx(build_agricultural_etax_export(agricultural_income_from_dataset()))
    )
    assert errors == [], f"unexpected XSD errors for KOA240: {errors}"

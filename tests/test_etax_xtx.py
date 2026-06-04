"""Real e-Tax ``.xtx`` (KOA210) rendering + official XSD validation — Issue #79.

Two layers, mirroring the issue's ACs:

* **Rendering / golden** (always run, no .xsd needed): the synthetic 決算書 renders to the nested
  KOA210 XML tree, deterministically, and equals the committed golden — so a regression in the
  renderer or the XSD-derived layout is caught offline.
* **形式妥当性 (XSD)** (gated on the fetched .xsd): the generated .xtx passes 国税庁の
  ``KOA210-011.xsd``, and deliberate 形式不正 (名前空間 / 必須属性 / 桁あふれ) are machine-detected.
  The official .xsd is 著作物 (非同梱); these tests skip when it has not been fetched (CI fetches it),
  exactly as the DB-backed tests skip without ``AI_BOOKS_DB_URL``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ai_books.etax import build_etax_export, export_etax, parse_etax_format, render_etax_xtx
from ai_books.etax.export import EtaxFormat
from tests.etax_xsd import skip_reason, validate_xtx, xsd_available
from tests.fixtures.seed_fy import etax_export_from_dataset, load_golden
from tests.fixtures.seed_fy.reports import financial_statements_from_dataset

_NS = "http://xml.e-tax.nta.go.jp/XSD/shotoku"
_requires_xsd = pytest.mark.skipif(not xsd_available(), reason=skip_reason())


def _q(tag: str) -> str:
    """Qualify a 項目コード with the shotoku namespace for ElementTree lookups."""
    return f"{{{_NS}}}{tag}"


# --- rendering / structure ------------------------------------------------------


def test_xtx_root_carries_form_attribute_and_namespace() -> None:
    xtx = render_etax_xtx(etax_export_from_dataset())
    assert xtx.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xtx)
    assert root.tag == _q("KOA210")
    # gen:FormAttribute は use="required": VR / softNM / sakuseiNM / sakuseiDay が揃う.
    assert root.attrib["VR"] == "11.0"
    assert root.attrib["softNM"]
    assert root.attrib["sakuseiNM"]
    assert root.attrib["sakuseiDay"] == "2025-12-31"  # 期末日 (決定的, not 今日)


def test_xtx_nests_items_under_their_form_tree() -> None:
    # 売上金額 AMF00100 sits at KOA210-1 > AMF00000 > AMF00010 > AMF00090, not at the root.
    root = ET.fromstring(render_etax_xtx(etax_export_from_dataset()))
    sales = root.find(
        f"./{_q('KOA210-1')}/{_q('AMF00000')}/{_q('AMF00010')}/{_q('AMF00090')}/{_q('AMF00100')}"
    )
    assert sales is not None
    assert sales.text == "1650000"
    # 売上原価 は AMF00110 グループ配下に 期首/仕入/期末/差引原価 を順に持つ.
    cogs = root.find(f".//{_q('AMF00110')}")
    assert cogs is not None
    assert [c.tag for c in cogs] == [
        _q(c) for c in ("AMF00120", "AMF00130", "AMF00150", "AMF00160")
    ]


def test_xtx_depreciation_is_a_repeating_block_per_row() -> None:
    # 減価償却費 は AMF01600 の繰返しブロック: seed の 2 行 → 2 つの AMF01600.
    root = ET.fromstring(render_etax_xtx(etax_export_from_dataset()))
    blocks = root.findall(f".//{_q('AMF01600')}")
    assert len(blocks) == 2
    first = blocks[0]
    # 名称(AMF01610) と 各金額が同じ occurrence 内に入る.
    assert first.find(_q("AMF01610")) is not None
    assert first.find(_q("AMF01640")) is not None


def test_xtx_months_are_distinct_groups() -> None:
    root = ET.fromstring(render_etax_xtx(etax_export_from_dataset()))
    # 2月 (AMF00620) > 売上 AMF00630 / 仕入 AMF00640.
    feb = root.find(f".//{_q('AMF00620')}")
    assert feb is not None
    assert feb.find(_q("AMF00630")) is not None


def test_xtx_is_deterministic() -> None:
    fs = financial_statements_from_dataset()
    assert export_etax(fs, fmt=EtaxFormat.XTX) == export_etax(fs, fmt=EtaxFormat.XTX)


def test_export_etax_dispatches_xtx_format() -> None:
    fs = financial_statements_from_dataset()
    assert parse_etax_format("xtx") is EtaxFormat.XTX
    out = export_etax(fs, fmt=EtaxFormat.XTX)
    assert out.startswith("<?xml")
    assert "<KOA210" in out


def test_xtx_rejects_non_koa210_spec() -> None:
    # 合成様式 (PL010…) は KOA210 layout に無いコードなので .xtx には落とせない (fail loud).
    synthetic = build_etax_export(financial_statements_from_dataset(), version="synthetic")
    with pytest.raises(ValueError, match="not in the KOA210 layout"):
        render_etax_xtx(synthetic)


def test_xtx_overflow_emits_named_additional_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    # 追加科目枠 (slots): 固定行に無い経費は AMF00355 繰返しに 科目名(AMF00060)+金額(AMF00360) で入る.
    from ai_books.reports import financial_statements_snapshot

    fs = financial_statements_from_dataset()
    snapshot = financial_statements_snapshot(fs)
    snapshot["profit_and_loss"]["selling_admin_expenses"]["lines"].extend(
        [
            {"code": "7901", "name": "研究開発費", "amount": "12000", "category": "x"},
            {"code": "7902", "name": "支払手数料", "amount": "3400", "category": "x"},
        ]
    )
    monkeypatch.setattr("ai_books.etax.export.financial_statements_snapshot", lambda _: snapshot)

    root = ET.fromstring(render_etax_xtx(build_etax_export(fs)))
    slots = root.findall(f".//{_q('AMF00355')}")
    assert len(slots) == 2
    first_name = slots[0].find(_q("AMF00060"))
    first_amount = slots[0].find(_q("AMF00360"))
    assert first_name is not None
    assert first_name.text == "研究開発費"
    assert first_amount is not None
    assert first_amount.text == "12000"


# --- golden (offline regression guard) ------------------------------------------


def test_xtx_matches_committed_golden() -> None:
    snapshot = {
        "report": "etax_xtx",
        "form_id": "KOA210",
        "version": "11.0",
        "namespace": _NS,
        "xtx_lines": render_etax_xtx(etax_export_from_dataset()).splitlines(),
    }
    assert snapshot == load_golden("etax_xtx")


# --- 形式妥当性: official XSD validation (gated on the fetched .xsd) --------------


@_requires_xsd
def test_generated_xtx_passes_official_xsd() -> None:
    # AC: 生成 .xtx が 国税庁 .xsd のスキーマ検証を pass.
    errors = validate_xtx(render_etax_xtx(etax_export_from_dataset()))
    assert errors == [], f"unexpected XSD errors: {errors}"


@_requires_xsd
def test_overflow_xtx_still_passes_official_xsd(monkeypatch: pytest.MonkeyPatch) -> None:
    # 追加科目枠を使った .xtx も XSD 妥当 (繰返しブロックの構造が正しい).
    from ai_books.reports import financial_statements_snapshot

    fs = financial_statements_from_dataset()
    snapshot = financial_statements_snapshot(fs)
    snapshot["balance_sheet"]["assets"][0]["lines"].append(
        {"code": "1999", "name": "その他資産", "balance": "5000", "category": "x"}
    )
    monkeypatch.setattr("ai_books.etax.export.financial_statements_snapshot", lambda _: snapshot)
    assert validate_xtx(render_etax_xtx(build_etax_export(fs))) == []


@_requires_xsd
def test_wrong_namespace_is_detected() -> None:
    # AC: 形式不正 (名前空間) を機械検出.
    xtx = render_etax_xtx(etax_export_from_dataset())
    broken = xtx.replace(_NS, "http://example.com/not-etax")
    assert validate_xtx(broken) != []


@_requires_xsd
def test_missing_required_attribute_is_detected() -> None:
    # AC: 形式不正 (必須) を機械検出 — 必須属性 softNM を落とす.
    xtx = render_etax_xtx(etax_export_from_dataset())
    broken = xtx.replace(' softNM="ai-books"', "", 1)
    assert validate_xtx(broken) != []


@_requires_xsd
def test_digit_overflow_is_detected() -> None:
    # AC: 形式不正 (桁) を機械検出 — kingaku は最大15桁、16桁は不正.
    xtx = render_etax_xtx(etax_export_from_dataset())
    broken = xtx.replace("<AMF00100>1650000</AMF00100>", f"<AMF00100>{'1' * 16}</AMF00100>")
    assert validate_xtx(broken) != []

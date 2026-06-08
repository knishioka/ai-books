"""Data-driven, versioned e-Tax 様式 specification — Issue #24 / #78.

The e-Tax 取込様式 changes by 年度 (項目の増減・桁数・コード体系), so the mapping from the
青色申告決算書 to e-Tax records is kept as **data**, not branching code: a :class:`EtaxFormatSpec`
is an ordered list of field/section descriptors, each naming a *path* into the 決算書 snapshot
(:func:`ai_books.reports.financial_statements_snapshot`) plus the constraints that field must
satisfy. To support a new 年度 you register one more :class:`EtaxFormatSpec` in
:data:`ETAX_FORMAT_SPECS`; the export engine and the CSV/XML renderers never change.

Paths are dot-separated keys into the snapshot dict (``profit_and_loss.sales.subtotal``). A
numeric segment indexes a list (``monthly.rows.0.sales``); a segment ending in ``[]`` flattens a
list and descends into each element, so one section spec can gather 科目内訳 that live under
several sub-sections (``balance_sheet.assets[].lines`` walks every 資産 区分's lines).
:func:`resolve_scalar` reads a single value; :func:`resolve_list` reads a (possibly flattened)
list of row dicts.

Descriptor vocabulary (the engine dispatches on type, in declared order):

* :class:`EtaxScalarField` — one cell pulled from a dot-path.
* :class:`EtaxSection` — a *repeating* 内訳 block: one list source, the same per-column 項目コード
  emitted once per row (e.g. 減価償却費の計算).
* :class:`EtaxFixedSection` — the real 様式's *fixed 勘定科目行* shape (#78): the snapshot's free
  ``lines`` are routed by 勘定科目コード into a fixed set of 項目コード (経費・資産・負債…), amounts
  for the same target row summed. 勘定科目 with no fixed row spill into the 様式's 追加科目枠
  (``overflow``); a 科目 that matches neither a fixed row nor a free 枠 is an **未分類** error —
  that is how AC「網羅的にマッピング(未分類項目を検出)」is enforced.

## 令和7年分 (2025) — 実 様式 KOA210 青色申告決算書(一般用) v11.0

The 2025 spec maps the snapshot onto the **official 所得税関係 XML 様式** KOA210 (一般用, v11.0),
using the field catalog acquired in #76 (``docs/etax/field_catalog.json`` /
``docs/etax/snapshot_mapping.json``; 出所・版・SHA256 は ``docs/etax/manifest.json``). 製造原価の
計算 (AMH*) is **inlined** in KOA210, not a separate 帳票. Structural deltas from a naive 1:1
mapping (documented in #76 ``snapshot_mapping.json``) and how this spec resolves them:

* **経費・資産・負債 are fixed 勘定科目行**, not free 内訳 — handled by :class:`EtaxFixedSection`;
  科目 outside the fixed set go to the 様式's 追加科目枠 (rep-limited), overflow ⇒ 未分類エラー.
* **売上原価 is the fixed 期首+仕入-期末 calculation**: 期首(AMF00120)/期末(AMF00150) come from
  their specific 棚卸 科目, every other COGS 科目 (商品仕入 + 製造原価内訳) accumulates into
  仕入金額(製品製造原価) AMF00130, so 期首+仕入-期末 = 差引原価(AMF00160) holds.
* **貸借対照表は期首・期末の2列**だが snapshot は期末残高のみ → 期首列は emit しない (任意欄)。
* **負債と純資産は「負債・資本の部」に統合** — one :class:`EtaxFixedSection` over both
  ``liabilities`` and ``equity`` lines; 青色申告特別控除前所得(AMG00750) carries net_income so
  the 部 合計(AMG00760) balances to 資産合計.
* **損益に段階表示(営業利益/営業外/経常利益)が無い**: KOA210(一般用) は 営業外 の段を持たないが、
  個々の 営業外 科目には 様式上の居場所がある場合とない場合がある。#83 で **営業外の扱いを方針として固定**:
  - **様式に居場所がある 営業外費用** (利子割引料 8210 → 経費 AMF00330) は :class:`EtaxFixedSection`
    (``PL_NON_OP_EXPENSES``, ``overflow_mode="drop"``) で 経費 セルへ橋渡しし、その routed total を
    :class:`EtaxComputedField` で 経費計(AMF00380) に加算・差引金額２(AMF00390) から減算する。これで
    様式の内訳整合 (経費行合計 = 経費計 / 差引金額１ - 経費計 = 差引金額２) が保たれる。
  - **様式に居場所がない 営業外** (受取利息 8110・雑収入 8120 等の 営業外収益、雑損失 8220 等) は
    意図的に未マッピング(drop)。その net 効果は net_income 経由で 所得(AMF00500) が carry する
    (差引金額２→所得 の残差 = 非居場所 営業外 net。様式上は 各種引当金 欄が埋めるべき差で、本実装は空欄)。
  営業外の段を独立に持つ 様式/年度 が要るときは、その様式専用の spec を別途登録する。
* **ヘッダ必須メタ (元号/年分・住所/氏名・提出年月日・業種名…) と BS 期首列** are not in the
  snapshot; this spec focuses on **value mapping** (#78) and leaves those 任意欄 empty. 形式
  (.xsd) 妥当性は #79.

The earlier *synthetic* (非公式・教育用) layout is kept off the 年度 axis under the
``"synthetic"`` version key so its mapping/validation/golden machinery still runs without being
mistaken for the real 様式.

## KOA220(不動産所得用) / KOA240(農業所得用) — 収入側 spec 登録済 (#126 stage 4)

項目カタログは取得済み (#76 ``field_catalog.json``: KOA220=226 / KOA240=357 項目)。
:func:`~ai_books.reports.financial_statements_snapshot` は KOA210(一般用) 向けで、不動産賃貸料収入・
農産物売上等の所得固有データは供給しない。両様式の **収入側 data-supply** は実装済: KOA220 は #124/#127
(:func:`~ai_books.reports.real_estate_income_snapshot` — 不動産所得の収入の内訳 / 地代家賃の内訳 /
借入金利子の内訳)、KOA240 は #125/#128 (:func:`~ai_books.reports.agricultural_income_snapshot` — 農産物の
収入の内訳 / 畜産物その他 / 雑収入 / 収入金額 / 未収穫農産物 / 販売用動物 / 育成費用の明細); いずれも金額は
仕訳から集計、記述メタは fixture。

#126 (stage 4) は **その収入側 snapshot を 様式の 内訳ブロックへ写す** :class:`EtaxFormatSpec` を登録する
(:data:`_SPEC_2025_KOA220` / :data:`_SPEC_2025_KOA240`)。様式の三〜七つの 内訳 はいずれも 繰返しブロック
(KOA220: 賃貸 ANF00340 / 地代 ANF01160 / 利子 ANF01260; KOA240: 農産物 APF00680 / 畜産 APF01100 / 雑収入
APF01200 / 棚卸 APF01250・01330 / 育成 APF02320) なので :class:`EtaxSection` で表し、各 計 と 収入金額 summary
は :class:`EtaxScalarField`。engine は KOA210 と同じ data-driven のまま — 違うのは入力 snapshot だけで、入口は
:func:`~ai_books.etax.export.build_real_estate_etax_export` /
:func:`~ai_books.etax.export.build_agricultural_etax_export` が選ぶ (KOA210 は
:func:`~ai_books.etax.export.build_etax_export`)。version キーは ``2025-KOA220`` / ``2025-KOA240``
(KOA210 の ``2025`` と別)。

経費・貸借対照表・減価償却 (KOA220 ANF00120 / ANG\\* / ANF00880; KOA240 APF00190 / 減価償却 APF02040) は
KOA210 と同じ 決算書 snapshot を :class:`EtaxFixedSection` (#78) / :class:`EtaxComputedField` (#83) で流用
できるが、本様式向けの 経費/BS data-supply は未実装のため stage 4 の対象外 (収入側のみ)。詳細は
``docs/etax/README.md`` の「様式別 spec 実装状況」を参照。

数量・面積 (作付面積 APF00700 等) は 様式上 小数許容 (``Z,ZZZ,ZZZ.ZZ``) で 整数円 AMOUNT に乗らず、賃貸契約期間の
月 (KOA220 ANF00410/00420) は 和暦の複合型 (``gen:era``/``gen:yy`` を子に要求) で整数リーフに乗らない。よって本
spec は **金額 (整数円) と 区分・名称・数量表記 (文字)** のみを写す (いずれも収入金額には影響しない記述メタは除外)。
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ai_books.models import EtaxValueKind

#: e-Tax 金額項目 の整数部桁数上限. KOA210 金額の標準書式は ``Z,ZZZ,ZZZ,ZZZ,ZZZ`` = 整数13桁
#: (field catalog ``int_digits``). A 金額 wider than this fails validation.
DEFAULT_MAX_INT_DIGITS = 13


class EtaxScalarField(NamedTuple):
    """One scalar e-Tax 項目 — a single value pulled from the 決算書 snapshot by ``source`` path.

    ``form`` is the 面 区分; ``item_code`` the e-Tax 項目コード; ``source`` a dot-path into the
    snapshot (numeric segments index lists; no ``[]``). ``required`` decides whether a
    missing/empty value is a hard error; ``max_int_digits`` caps an ``AMOUNT``'s integer part.
    """

    form: str
    item_code: str
    label: str
    source: str
    kind: EtaxValueKind = EtaxValueKind.AMOUNT
    required: bool = True
    max_int_digits: int = DEFAULT_MAX_INT_DIGITS


class EtaxSectionField(NamedTuple):
    """One column of a repeating e-Tax 内訳 section — a value pulled from each row dict.

    ``source`` is a path *relative to each row* of the section's list (``amount`` / ``code`` /
    ``month``). ``is_account_code`` tags the column whose value is the row's 勘定科目コード, so the
    emitted :class:`~ai_books.models.EtaxRecord` can carry it for traceability.
    """

    item_code: str
    label: str
    source: str
    kind: EtaxValueKind = EtaxValueKind.AMOUNT
    required: bool = True
    max_int_digits: int = DEFAULT_MAX_INT_DIGITS
    is_account_code: bool = False


class EtaxSection(NamedTuple):
    """A repeating e-Tax 内訳 block — ``source`` names the list, ``fields`` its per-row columns.

    ``source`` is a path to a list in the snapshot, optionally using ``[]`` to flatten nested
    lists (``balance_sheet.assets[].lines``). Each list element yields one row's worth of records,
    one per :class:`EtaxSectionField`, in spec order. The same 項目コード is emitted once per row
    (the e-Tax repeating-occurrence model), distinguished by the record's 1-based ``row``.
    """

    form: str
    section_code: str
    label: str
    source: str
    fields: tuple[EtaxSectionField, ...]


class EtaxFixedRow(NamedTuple):
    """One fixed 勘定科目行 of the real 様式 — ``account_codes`` route into ``item_code``.

    Every snapshot ``line`` whose 勘定科目コード is in ``account_codes`` contributes (summed) to
    this row's amount — multiple 科目 can roll up into one 様式行 (e.g. 短期+長期借入金 → 借入金).
    ``sign`` adjusts the stored amount before summing: ``"abs"`` for 棚卸高 the snapshot stores as
    a contra (negative) but the 様式 prints positive; ``"neg"`` to flip; ``"as_is"`` (default)
    otherwise.
    """

    item_code: str
    label: str
    account_codes: tuple[str, ...]
    sign: str = "as_is"


class EtaxFixedSection(NamedTuple):
    """The real 様式's *fixed 勘定科目行* block (#78) — route snapshot ``lines`` by コード.

    ``sources`` are one or more list paths (``[]`` flattens) whose rows are gathered together;
    ``value_field`` is the row key holding the amount (``amount`` / ``balance``). Each gathered
    line is routed by its ``code`` into the matching :class:`EtaxFixedRow`; lines matching no fixed
    row spill into the 追加科目枠:

    * ``overflow_mode="slots"`` — each unmatched 科目 takes the next of ``overflow_max`` repeating
      ``overflow_code`` slots (the 様式's 追加科目, e.g. AMF00360 rep=6), carrying its own 科目名 /
      コード for traceability. Exhausting the slots is an **未分類** error. ``overflow_name_code`` is
      the sibling 科目名 タグ of that repeating slot (e.g. AMF00060 beside AMF00360); when set, each
      slot also emits a TEXT record so the .xtx 追加科目 (#79) carries its 科目名.
    * ``overflow_mode="accumulate"`` — all unmatched 科目 sum into a single ``overflow_code`` (e.g.
      売上原価's 仕入金額, which absorbs 商品仕入 + 製造原価内訳).
    * ``overflow_mode="drop"`` — unmatched 科目 are **intentionally discarded** (no record, no error).
      Used to *bridge* a 区分 the 様式 lacks: the 営業外費用 source feeds its homed 科目 (利子割引料 →
      経費 AMF00330) into fixed rows while 様式 に居場所が無い 営業外 (雑損失 等) drop out — their効果は
      net_income 経由で 所得(AMF00500) が carry する (#83 で方針固定)。これは「未分類エラー」とは別物
      (科目は帳簿上は正しく分類済み・当該様式に枠が無いだけ) なので fail-loud しない。

    A line whose ``code`` is present but malformed is rejected (不正コード検出, AC #24).
    """

    form: str
    section_code: str
    label: str
    sources: tuple[str, ...]
    value_field: str
    rows: tuple[EtaxFixedRow, ...]
    overflow_code: str | None = None
    overflow_label: str = "追加科目"
    overflow_max: int = 0
    overflow_mode: str = "slots"  # "slots" | "accumulate" | "drop"
    overflow_name_code: str | None = None  # 追加科目枠の 科目名 タグ (slots mode; .xtx #79)
    kind: EtaxValueKind = EtaxValueKind.AMOUNT
    max_int_digits: int = DEFAULT_MAX_INT_DIGITS


class EtaxComputedField(NamedTuple):
    """A scalar 項目 whose 値 is a base snapshot 金額 adjusted by other sections' routed totals.

    Some 様式 cells can't be read straight from one snapshot path because 帳簿 and 様式 draw a 区分 line
    in different places. KOA210(一般用) files 利子割引料 under **経費**, but our chart classifies it as
    **営業外費用** — so 経費計(AMF00380) must read 販管費 小計 *plus* the homed 営業外費用 the bridge
    :class:`EtaxFixedSection` routes into 経費, and 差引金額２(AMF00390) the same base *minus* it (so
    経費行合計 = 経費計 and 差引金額１ - 経費計 = 差引金額２ both hold on the form). ``base_source`` is the
    snapshot dot-path for the base 金額; ``add_sections`` / ``sub_sections`` name the ``section_code`` of
    **earlier** :class:`EtaxFixedSection`s whose *routed total* (Σ emitted 金額) is added / subtracted.
    Referenced sections must precede this field in ``items`` (the engine fills their totals as it walks);
    an absent / empty section contributes 0.
    """

    form: str
    item_code: str
    label: str
    base_source: str
    add_sections: tuple[str, ...] = ()
    sub_sections: tuple[str, ...] = ()
    kind: EtaxValueKind = EtaxValueKind.AMOUNT
    required: bool = True
    max_int_digits: int = DEFAULT_MAX_INT_DIGITS


#: One spec item is any descriptor; the engine dispatches on type in declared order.
EtaxItem = EtaxScalarField | EtaxSection | EtaxFixedSection | EtaxComputedField


class EtaxFormatSpec(NamedTuple):
    """A whole versioned e-Tax 様式 — its ordered field / section descriptors.

    ``items`` are emitted in declared order. Swapping the spec is the *only* thing that changes
    when the 様式 changes for a new 年度.
    """

    version: str
    form_id: str
    items: tuple[EtaxItem, ...]


# ── path resolution over the 決算書 snapshot ─────────────────────────────────────

#: Sentinel for "no value at this path" — distinct from a legitimately present ``None``.
MISSING: Any = object()


def _descend(node: Any, parts: list[str]) -> Any:
    """Walk ``parts`` into ``node``; numeric segments index lists, ``[]`` flattens one level.

    Returns :data:`MISSING` if any key/index is absent (or the shape is not walkable). When a
    ``[]`` segment is present the result is a list (flattened one level for every trailing segment
    that itself yields a list), otherwise a single value.
    """
    if not parts:
        return node
    head, *rest = parts
    flatten = head.endswith("[]")
    key = head[:-2] if flatten else head
    if not flatten and isinstance(node, list):
        if not key.isdigit():
            return MISSING
        index = int(key)
        if index >= len(node):
            return MISSING
        return _descend(node[index], rest)
    if not isinstance(node, dict) or key not in node:
        return MISSING
    value = node[key]
    if not flatten:
        return _descend(value, rest)
    if not isinstance(value, list):
        return MISSING
    collected: list[Any] = []
    for item in value:
        descended = _descend(item, rest)
        if descended is MISSING:
            return MISSING
        if rest and isinstance(descended, list):
            collected.extend(descended)
        else:
            collected.append(descended)
    return collected


def resolve_scalar(snapshot: dict[str, Any], path: str) -> Any:
    """Resolve a scalar ``path`` (no ``[]``) into the snapshot; :data:`MISSING` if absent."""
    return _descend(snapshot, path.split("."))


def resolve_list(snapshot: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """Resolve a list ``path`` (the section source) into a list of row dicts.

    A missing path resolves to an empty list (an absent 内訳 means zero rows, not an error — the
    per-row 必須 check still applies to whatever rows *are* present).
    """
    value = _descend(snapshot, path.split("."))
    if value is MISSING or not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


# ── 令和7年分 (2025) — 実 様式 KOA210 青色申告決算書(一般用) v11.0 ───────────────────

_AMOUNT = EtaxValueKind.AMOUNT
_TEXT = EtaxValueKind.TEXT

#: 1月..12月 の固定 項目コード (AMF00600/00610 …, +30 per month) ← snapshot ``monthly.rows[i]``.
#: The real 様式 has a distinct タグ per month (not a repeating block), so these are 24 scalars.
_MONTHLY_FIELDS: tuple[EtaxScalarField, ...] = tuple(
    field
    for month in range(1, 13)
    for base in (600 + (month - 1) * 30,)
    for field in (
        EtaxScalarField(
            "MONTHLY",
            f"AMF{base:05d}",
            f"{month}月 売上(収入)金額",
            f"monthly.rows.{month - 1}.sales",
            required=False,
        ),
        EtaxScalarField(
            "MONTHLY",
            f"AMF{base + 10:05d}",
            f"{month}月 仕入金額",
            f"monthly.rows.{month - 1}.purchases",
            required=False,
        ),
    )
)


#: 経費 固定行 — 勘定科目コード (seed/accounts.py 7xxx) → KOA210 経費 項目コード (AMF00190-00370).
_EXPENSE_ROWS: tuple[EtaxFixedRow, ...] = (
    EtaxFixedRow("AMF00190", "租税公課", ("7110",)),
    EtaxFixedRow("AMF00200", "荷造運賃", ("7120",)),
    EtaxFixedRow("AMF00210", "水道光熱費", ("7130",)),
    EtaxFixedRow("AMF00220", "旅費交通費", ("7140",)),
    EtaxFixedRow("AMF00230", "通信費", ("7150",)),
    EtaxFixedRow("AMF00240", "広告宣伝費", ("7160",)),
    EtaxFixedRow("AMF00250", "接待交際費", ("7170",)),
    EtaxFixedRow("AMF00260", "損害保険料", ("7180",)),
    EtaxFixedRow("AMF00270", "修繕費", ("7190",)),
    EtaxFixedRow("AMF00280", "消耗品費", ("7200",)),
    EtaxFixedRow("AMF00290", "減価償却費", ("7210",)),
    EtaxFixedRow("AMF00300", "福利厚生費", ("7220",)),
    EtaxFixedRow("AMF00310", "給料賃金", ("7230",)),
    EtaxFixedRow("AMF00320", "外注工賃", ("7240",)),
    EtaxFixedRow("AMF00340", "地代家賃", ("7250",)),
    EtaxFixedRow("AMF00370", "雑費", ("7290",)),
)


#: 営業外費用 のうち 様式に居場所がある 科目 → KOA210 経費 項目コード (#83 営業外マッピング方針)。
#: 帳簿では 営業外費用 (8xxx) だが KOA210(一般用) は 利子割引料 を 経費 AMF00330 に置く。橋渡しは
#: ``overflow_mode="drop"`` の :class:`EtaxFixedSection` で行い、ここに無い 営業外 (雑損失 等) は drop。
_NON_OPERATING_EXPENSE_ROWS: tuple[EtaxFixedRow, ...] = (
    EtaxFixedRow("AMF00330", "利子割引料", ("8210",)),
)


#: 資産の部(期末) 固定行 — 勘定科目コード (1xxx) → KOA210 資産 項目コード (AMG00260-00430).
_ASSET_ROWS: tuple[EtaxFixedRow, ...] = (
    EtaxFixedRow("AMG00260", "現金", ("1110",)),
    EtaxFixedRow("AMG00270", "当座預金", ("1130",)),
    EtaxFixedRow("AMG00280", "定期預金", ("1142",)),
    EtaxFixedRow("AMG00290", "その他の預金", ("1140", "1141")),
    EtaxFixedRow("AMG00300", "受取手形", ("1150",)),
    EtaxFixedRow("AMG00310", "売掛金", ("1160",)),
    EtaxFixedRow("AMG00320", "有価証券", ("1170",)),
    EtaxFixedRow("AMG00330", "棚卸資産", ("1180",)),
    EtaxFixedRow("AMG00340", "前払金", ("1190",)),
    EtaxFixedRow("AMG00350", "貸付金", ("1200",)),
    EtaxFixedRow("AMG00360", "建物", ("1510",)),
    EtaxFixedRow("AMG00370", "建物附属設備", ("1520",)),
    EtaxFixedRow("AMG00380", "機械装置", ("1530",)),
    EtaxFixedRow("AMG00390", "車両運搬具", ("1540",)),
    EtaxFixedRow("AMG00400", "工具・器具・備品", ("1550",)),
    EtaxFixedRow("AMG00410", "土地", ("1560",)),
    EtaxFixedRow("AMG00430", "事業主貸", ("1290",)),
)


#: 負債・資本の部(期末) 固定行 — 負債(2xxx) と 純資産(3xxx) を統合した 様式区分。
#: 借入金(AMG00660) は 短期(2160)+長期(2510) を合算。青色申告特別控除前所得(AMG00750) は
#: net_income を別途 emit するため固定行には含めない (これで 部 合計 = 資産合計 が一致)。
_LIABILITY_EQUITY_ROWS: tuple[EtaxFixedRow, ...] = (
    EtaxFixedRow("AMG00640", "支払手形", ("2110",)),
    EtaxFixedRow("AMG00650", "買掛金", ("2120",)),
    EtaxFixedRow("AMG00660", "借入金", ("2160", "2510")),
    EtaxFixedRow("AMG00670", "未払金", ("2130",)),
    EtaxFixedRow("AMG00680", "前受金", ("2140",)),
    EtaxFixedRow("AMG00690", "預り金", ("2150",)),
    EtaxFixedRow("AMG00730", "事業主借", ("3120",)),
    EtaxFixedRow("AMG00740", "元入金", ("3110",)),
)


#: 製造原価 その他経費 固定行 — 製造間接費(63xx) → KOA210 製造原価 項目コード (AMH00100-00140)。
_MANUFACTURING_OVERHEAD_ROWS: tuple[EtaxFixedRow, ...] = (
    EtaxFixedRow("AMH00100", "外注工賃", ("6310",)),
    EtaxFixedRow("AMH00110", "電力費", ("6320",)),
    EtaxFixedRow("AMH00130", "修繕費", ("6340",)),
    EtaxFixedRow("AMH00140", "減価償却費", ("6330",)),
)


_SPEC_2025 = EtaxFormatSpec(
    version="2025",
    form_id="青色申告決算書(一般用)",
    items=(
        # ── 損益計算書 (1ページ目) ──
        EtaxScalarField("PL", "AMF00100", "売上(収入)金額", "profit_and_loss.sales.subtotal"),
        # 売上原価: 期首(AMF00120) + 仕入(AMF00130, 商品仕入+製造原価内訳の合算) - 期末(AMF00150)
        # = 差引原価(AMF00160). 期末は contra (snapshot は負) を abs で正値化.
        EtaxFixedSection(
            "PL",
            "PL_COGS",
            "売上原価",
            ("profit_and_loss.cost_of_goods_sold.lines",),
            "amount",
            (
                EtaxFixedRow("AMF00120", "期首商品(製品)棚卸高", ("5110", "6110")),
                EtaxFixedRow("AMF00150", "期末商品(製品)棚卸高", ("5130", "6130"), sign="abs"),
            ),
            overflow_code="AMF00130",
            overflow_label="仕入金額(製品製造原価)",
            overflow_mode="accumulate",
        ),
        EtaxScalarField(
            "PL", "AMF00160", "差引原価", "profit_and_loss.cost_of_goods_sold.subtotal"
        ),
        EtaxScalarField("PL", "AMF00170", "差引金額１", "profit_and_loss.gross_profit"),
        # 経費: 固定勘定科目行 (AMF00190-00370) + 追加科目枠 AMF00360 (rep=6).
        EtaxFixedSection(
            "PL",
            "PL_EXPENSES",
            "経費",
            ("profit_and_loss.selling_admin_expenses.lines",),
            "amount",
            _EXPENSE_ROWS,
            overflow_code="AMF00360",
            overflow_label="追加科目の金額",
            overflow_max=6,
            overflow_name_code="AMF00060",
        ),
        # 営業外費用→経費 橋渡し: 利子割引料(8210) を 経費 AMF00330 へ; 居場所の無い 営業外 は drop (#83).
        EtaxFixedSection(
            "PL",
            "PL_NON_OP_EXPENSES",
            "営業外費用(様式上は経費)",
            ("profit_and_loss.non_operating_expenses.lines",),
            "amount",
            _NON_OPERATING_EXPENSE_ROWS,
            overflow_mode="drop",
        ),
        # 経費計 = 販管費小計 + 橋渡しした 営業外費用 (利子割引料) → 経費行合計と一致.
        EtaxComputedField(
            "PL",
            "AMF00380",
            "経費 計",
            "profit_and_loss.selling_admin_expenses.subtotal",
            add_sections=("PL_NON_OP_EXPENSES",),
        ),
        # 差引金額２ = 営業利益 - 橋渡しした 営業外費用 → 差引金額１ - 経費計 と一致.
        EtaxComputedField(
            "PL",
            "AMF00390",
            "差引金額２",
            "profit_and_loss.operating_income",
            sub_sections=("PL_NON_OP_EXPENSES",),
        ),
        EtaxScalarField(
            "PL", "AMF00500", "青色申告特別控除前の所得金額", "profit_and_loss.net_income"
        ),
        # ── 月別売上(収入)金額及び仕入金額 (1月 AMF00600 … 12月 AMF00940) ──
        *_MONTHLY_FIELDS,
        EtaxScalarField("MONTHLY", "AMF00980", "月別売上(収入)金額(計)", "monthly.sales_total"),
        EtaxScalarField("MONTHLY", "AMF00990", "月別仕入金額(計)", "monthly.purchases_total"),
        # ── 減価償却費の計算 (繰返しブロック AMF016xx) ──
        EtaxSection(
            "DEPRECIATION",
            "DEPRECIATION_LINES",
            "減価償却費の計算",
            "depreciation.lines",
            (
                EtaxSectionField("AMF01610", "減価償却資産の名称等", "name", _TEXT),
                EtaxSectionField("AMF01640", "取得価額", "acquisition_cost"),
                EtaxSectionField("AMF01750", "本年分の償却費合計", "depreciation_expense"),
                EtaxSectionField("AMF01780", "未償却残高", "closing_book_value"),
            ),
        ),
        EtaxScalarField(
            "DEPRECIATION",
            "AMF01830",
            "本年分の償却費合計(計)",
            "depreciation.total_depreciation",
        ),
        EtaxScalarField(
            "DEPRECIATION",
            "AMF01840",
            "本年分の必要経費算入額(計)",
            "depreciation.expense_total",
            required=False,
        ),
        # ── 製造原価の計算 (KOA210 内包, AMH*) ──
        # 原材料費: 期首(AMH00030) + 仕入(AMH00040, 棚卸以外を合算) - 期末(AMH00060) → 差引原材料費.
        EtaxFixedSection(
            "MANUFACTURING",
            "MC_MATERIALS",
            "原材料費",
            ("manufacturing_cost.materials.lines",),
            "amount",
            (
                EtaxFixedRow("AMH00030", "期首原材料棚卸高", ("6110",)),
                EtaxFixedRow("AMH00060", "期末原材料棚卸高", ("6130",), sign="abs"),
            ),
            overflow_code="AMH00040",
            overflow_label="原材料仕入高",
            overflow_mode="accumulate",
        ),
        EtaxScalarField(
            "MANUFACTURING", "AMH00070", "差引原材料費", "manufacturing_cost.materials.subtotal"
        ),
        EtaxScalarField("MANUFACTURING", "AMH00080", "労務費", "manufacturing_cost.labor.subtotal"),
        # その他の製造経費: 固定行 (AMH00100-00140) + 追加科目枠 AMH00150 (rep=8).
        EtaxFixedSection(
            "MANUFACTURING",
            "MC_OVERHEAD",
            "その他の製造経費",
            ("manufacturing_cost.overhead.lines",),
            "amount",
            _MANUFACTURING_OVERHEAD_ROWS,
            overflow_code="AMH00150",
            overflow_label="追加科目の金額",
            overflow_max=8,
            overflow_name_code="AMH00010",
        ),
        EtaxScalarField(
            "MANUFACTURING",
            "AMH00170",
            "その他の製造経費 計",
            "manufacturing_cost.overhead.subtotal",
        ),
        EtaxScalarField(
            "MANUFACTURING",
            "AMH00180",
            "総製造費",
            "manufacturing_cost.total_manufacturing_cost",
        ),
        EtaxScalarField(
            "MANUFACTURING",
            "AMH00220",
            "製品製造原価",
            "manufacturing_cost.cost_of_goods_manufactured",
        ),
        # ── 貸借対照表 (期末) ──
        EtaxFixedSection(
            "BS",
            "BS_ASSETS",
            "資産の部(期末)",
            ("balance_sheet.assets[].lines",),
            "balance",
            _ASSET_ROWS,
            overflow_code="AMG00420",
            overflow_label="追加科目の金額",
            overflow_max=7,
            overflow_name_code="AMG00030",
        ),
        EtaxScalarField("BS", "AMG00440", "資産の部(期末)合計", "balance_sheet.total_assets"),
        EtaxFixedSection(
            "BS",
            "BS_LIABILITIES_EQUITY",
            "負債・資本の部(期末)",
            ("balance_sheet.liabilities[].lines", "balance_sheet.equity[].lines"),
            "balance",
            _LIABILITY_EQUITY_ROWS,
            overflow_code="AMG00700",
            overflow_label="追加科目の金額",
            overflow_max=7,
            overflow_name_code="AMG00470",
        ),
        EtaxScalarField(
            "BS", "AMG00750", "青色申告特別控除前の所得金額", "balance_sheet.net_income"
        ),
        EtaxScalarField("BS", "AMG00760", "負債・資本の部(期末)合計", "balance_sheet.total_assets"),
    ),
)


# ── 令和7年分 (2025) — 実 様式 KOA220 青色申告決算書(不動産所得用) v8.0 ─────────────
#
# 収入側 data-supply (#124/#127, ``real_estate_income_snapshot``) を 様式の 内訳 へ写す。三つの 内訳 は
# いずれも 様式上の 繰返しブロック (賃貸 ANF00340 / 地代家賃 ANF01160 / 借入金利子 ANF01260) なので
# :class:`EtaxSection`、各 計 (ANF00560 賃貸計 等) は :class:`EtaxScalarField`。金額は format ``Z,ZZZ,…``
# = 整数円 (AMOUNT)。賃貸契約期間の 月 は和暦の複合型なので写さない。経費/BS/減価償却は未供給で対象外。

_SPEC_2025_KOA220 = EtaxFormatSpec(
    version="2025-KOA220",
    form_id="青色申告決算書(不動産所得用)",
    items=(
        # 不動産所得の収入の内訳 — 物件ごとの繰返しブロック ANF00340 (賃貸料年額/礼金/権利金…).
        EtaxSection(
            "RENTAL_INCOME",
            "RE_RENTAL_LINES",
            "不動産所得の収入の内訳",
            "rental_income.lines",
            (
                EtaxSectionField(
                    "ANF00350", "貸家貸地等の別", "property_type", _TEXT, required=False
                ),
                EtaxSectionField("ANF00355", "用途", "usage", _TEXT, required=False),
                EtaxSectionField("ANF00360", "不動産の所在地", "location", _TEXT, required=False),
                EtaxSectionField(
                    "ANF00380", "賃借人の住所", "tenant_address", _TEXT, required=False
                ),
                EtaxSectionField("ANF00390", "賃借人の氏名", "tenant_name", _TEXT, required=False),
                # 賃貸契約期間 (自/至 月 ANF00410/ANF00420) は様式上 和暦の複合型 (gen:era/yy を子に要求)
                # で整数リーフに乗らないため 写さない (収入金額には影響しない契約メタ)。
                EtaxSectionField("ANF00500", "賃貸料年額", "rent_annual"),
                EtaxSectionField("ANF00510", "礼金", "key_money"),
                EtaxSectionField("ANF00520", "権利金", "right_money"),
                EtaxSectionField("ANF00530", "更新料", "renewal_fee"),
                EtaxSectionField("ANF00540", "名義書換料その他", "name_change_other"),
                EtaxSectionField("ANF00550", "保証金・敷金", "deposit"),
            ),
        ),
        # 不動産所得の収入の内訳 計 (ANF00560).
        EtaxScalarField(
            "RENTAL_INCOME", "ANF00570", "賃貸料年額 計", "rental_income.rent_annual_total"
        ),
        EtaxScalarField(
            "RENTAL_INCOME",
            "ANF00580",
            "礼金・権利金・更新料 計",
            "rental_income.key_right_renewal_total",
        ),
        EtaxScalarField(
            "RENTAL_INCOME",
            "ANF00590",
            "名義書換料その他 計",
            "rental_income.name_change_other_total",
        ),
        EtaxScalarField(
            "RENTAL_INCOME", "ANF00600", "保証金・敷金 計", "rental_income.deposit_total"
        ),
        # 地代家賃の内訳 — 繰返しブロック ANF01160 (支払先/賃借物件/賃借料/必要経費算入額).
        EtaxSection(
            "RENT_PAID",
            "RE_RENT_PAID_LINES",
            "地代家賃の内訳",
            "rent_paid.lines",
            (
                EtaxSectionField(
                    "ANF01180", "支払先の住所", "payee_address", _TEXT, required=False
                ),
                EtaxSectionField("ANF01190", "支払先の氏名", "payee_name", _TEXT, required=False),
                EtaxSectionField("ANF01200", "賃借物件", "leased_property", _TEXT, required=False),
                EtaxSectionField("ANF01220", "権利金", "right_money"),
                EtaxSectionField("ANF01230", "更新料", "renewal_fee"),
                EtaxSectionField("ANF01240", "賃借料", "rent"),
                EtaxSectionField("ANF01250", "賃借料のうち必要経費算入額", "deductible_expense"),
            ),
        ),
        # 借入金利子の内訳 — 繰返しブロック ANF01260 (支払先/期末残高/利子/必要経費算入額).
        EtaxSection(
            "LOAN_INTEREST",
            "RE_LOAN_LINES",
            "借入金利子の内訳",
            "loan_interest.lines",
            (
                EtaxSectionField(
                    "ANF01280", "支払先の住所", "payee_address", _TEXT, required=False
                ),
                EtaxSectionField("ANF01290", "支払先の氏名", "payee_name", _TEXT, required=False),
                EtaxSectionField("ANF01300", "期末現在の借入金等の金額", "year_end_balance"),
                EtaxSectionField("ANF01310", "本年中の借入金利子", "interest_paid"),
                EtaxSectionField("ANF01320", "うち必要経費算入額", "deductible_interest"),
            ),
        ),
    ),
)


# ── 令和7年分 (2025) — 実 様式 KOA240 青色申告決算書(農業所得用) v8.0 ───────────────
#
# 収入側 data-supply (#125/#128, ``agricultural_income_snapshot``) を 様式の 内訳 へ写す。農産物 (APF00680)・
# 畜産物 (APF01100)・雑収入 (APF01200)・棚卸明細 (未収穫 APF01250 / 販売用動物 APF01330)・育成費用 (APF02320)
# はいずれも 繰返しブロック → :class:`EtaxSection`、各 計 と 収入金額 summary (page1 APF00110-00180) は
# :class:`EtaxScalarField`。農産物の三区分 (田畑/果樹/特殊施設) は様式上 別ブロックだが、snapshot は 1 リストに
# ``category`` を持つので 先頭ブロック APF00680 に区分付きで写す。数量・面積は小数許容で整数円に乗らないため除外。

_SPEC_2025_KOA240 = EtaxFormatSpec(
    version="2025-KOA240",
    form_id="青色申告決算書(農業所得用)",
    items=(
        # 農産物の収入の内訳 — 作物ごとの繰返しブロック APF00680 (区分で田畑/果樹/特殊施設を識別).
        EtaxSection(
            "FARM_PRODUCTS",
            "AG_CROP_LINES",
            "農産物の収入の内訳",
            "farm_products.lines",
            (
                EtaxSectionField("APF00690", "区分", "category", _TEXT, required=False),
                EtaxSectionField(
                    "APF00740", "農産物の期首棚卸高(金額)", "opening_inventory_amount"
                ),
                EtaxSectionField("APF00750", "販売金額", "sales_amount"),
                EtaxSectionField("APF00760", "家事消費・事業消費金額", "home_consumption"),
                EtaxSectionField(
                    "APF00790", "農産物の期末棚卸高(金額)", "closing_inventory_amount"
                ),
            ),
        ),
        # 農産物の収入の内訳 計 (APF01040).
        EtaxScalarField(
            "FARM_PRODUCTS",
            "APF01060",
            "農産物 期首棚卸高 計",
            "farm_products.opening_inventory_total",
        ),
        EtaxScalarField(
            "FARM_PRODUCTS", "APF01070", "農産物 販売金額 計", "farm_products.sales_total"
        ),
        EtaxScalarField(
            "FARM_PRODUCTS",
            "APF01080",
            "農産物 家事消費等 計",
            "farm_products.home_consumption_total",
        ),
        EtaxScalarField(
            "FARM_PRODUCTS",
            "APF01090",
            "農産物 期末棚卸高 計",
            "farm_products.closing_inventory_total",
        ),
        # 畜産物その他の収入の内訳 — 繰返しブロック APF01100.
        EtaxSection(
            "LIVESTOCK",
            "AG_LIVESTOCK_LINES",
            "畜産物その他の収入の内訳",
            "livestock.lines",
            (
                EtaxSectionField("APF01110", "区分", "category_name", _TEXT, required=False),
                EtaxSectionField("APF01140", "販売金額", "sales_amount"),
                EtaxSectionField("APF01150", "家事消費・事業消費金額", "home_consumption"),
            ),
        ),
        EtaxScalarField("LIVESTOCK", "APF01170", "畜産物 販売金額 計", "livestock.sales_total"),
        EtaxScalarField(
            "LIVESTOCK", "APF01180", "畜産物 家事消費等 計", "livestock.home_consumption_total"
        ),
        # 雑収入の内訳 — 繰返しブロック APF01200.
        EtaxSection(
            "MISC_INCOME",
            "AG_MISC_LINES",
            "雑収入の内訳",
            "misc_income.lines",
            (
                EtaxSectionField("APF01210", "区分", "category_name", _TEXT, required=False),
                EtaxSectionField("APF01220", "金額", "amount"),
            ),
        ),
        EtaxScalarField("MISC_INCOME", "APF01230", "雑収入 合計金額", "misc_income.total"),
        # 収入金額 (page1 損益 summary APF00110-00180).
        EtaxScalarField("INCOME", "APF00110", "販売金額", "income.sales_amount_total"),
        EtaxScalarField(
            "INCOME", "APF00120", "家事消費・事業消費金額", "income.home_consumption_total"
        ),
        EtaxScalarField("INCOME", "APF00130", "雑収入", "income.misc_income_total"),
        EtaxScalarField("INCOME", "APF00140", "小計", "income.subtotal"),
        EtaxScalarField(
            "INCOME", "APF00160", "農産物の棚卸高(期首)", "income.opening_inventory_total"
        ),
        EtaxScalarField(
            "INCOME", "APF00170", "農産物の棚卸高(期末)", "income.closing_inventory_total"
        ),
        EtaxScalarField("INCOME", "APF00180", "収入金額(計)", "income.gross_income"),
        # 未収穫農産物 — 棚卸明細 繰返しブロック APF01250 (数量は文字).
        EtaxSection(
            "UNHARVESTED",
            "AG_UNHARVESTED_LINES",
            "未収穫農産物",
            "unharvested.lines",
            (
                EtaxSectionField("APF01260", "区分", "category_name", _TEXT, required=False),
                EtaxSectionField(
                    "APF01280", "期首棚卸高(数量)", "opening_qty", _TEXT, required=False
                ),
                EtaxSectionField("APF01290", "期首棚卸高(金額)", "opening_amount"),
                EtaxSectionField(
                    "APF01310", "期末棚卸高(数量)", "closing_qty", _TEXT, required=False
                ),
                EtaxSectionField("APF01320", "期末棚卸高(金額)", "closing_amount"),
            ),
        ),
        # 販売用動物等 — 棚卸明細 繰返しブロック APF01330 (数量は文字).
        EtaxSection(
            "SALE_ANIMALS",
            "AG_SALE_ANIMAL_LINES",
            "販売用動物等",
            "sale_animals.lines",
            (
                EtaxSectionField("APF01340", "区分", "category_name", _TEXT, required=False),
                EtaxSectionField(
                    "APF01360", "期首棚卸高(数量)", "opening_qty", _TEXT, required=False
                ),
                EtaxSectionField("APF01370", "期首棚卸高(金額)", "opening_amount"),
                EtaxSectionField(
                    "APF01390", "期末棚卸高(数量)", "closing_qty", _TEXT, required=False
                ),
                EtaxSectionField("APF01400", "期末棚卸高(金額)", "closing_amount"),
            ),
        ),
        # 果樹・牛馬等の育成費用の計算 — 繰返しブロック APF02320.
        EtaxSection(
            "CULTIVATION",
            "AG_CULTIVATION_LINES",
            "果樹・牛馬等の育成費用の計算",
            "cultivation_cost.lines",
            (
                EtaxSectionField("APF02330", "果樹・牛馬等の名称", "name", _TEXT, required=False),
                EtaxSectionField("APF02350", "前年からの繰越額", "opening_carryover"),
                EtaxSectionField("APF02370", "本年中の種苗費・種付料・素畜費", "seedling_cost"),
                EtaxSectionField("APF02380", "本年中の肥料・農薬等の投下費用", "fertilizer_cost"),
                EtaxSectionField("APF02390", "小計", "subtotal"),
                EtaxSectionField(
                    "APF02400", "育成中の果樹等から生じた収入金額", "income_from_growing"
                ),
                EtaxSectionField(
                    "APF02410", "本年に取得価額に加算する金額", "added_to_acquisition_cost"
                ),
                EtaxSectionField(
                    "APF02420", "本年中に成熟したものの取得価額", "matured_acquisition_cost"
                ),
                EtaxSectionField("APF02430", "翌年への繰越額", "carryover_to_next"),
            ),
        ),
        # 果樹・牛馬等の育成費用 計 (APF02440).
        EtaxScalarField(
            "CULTIVATION",
            "APF02450",
            "前年からの繰越額 計",
            "cultivation_cost.opening_carryover_total",
        ),
        EtaxScalarField(
            "CULTIVATION", "APF02470", "種苗費等 計", "cultivation_cost.seedling_cost_total"
        ),
        EtaxScalarField(
            "CULTIVATION", "APF02480", "肥料・農薬等 計", "cultivation_cost.fertilizer_cost_total"
        ),
        EtaxScalarField("CULTIVATION", "APF02490", "小計 計", "cultivation_cost.subtotal_total"),
        EtaxScalarField(
            "CULTIVATION",
            "APF02500",
            "育成中の収入金額 計",
            "cultivation_cost.income_from_growing_total",
        ),
        EtaxScalarField(
            "CULTIVATION",
            "APF02510",
            "取得価額に加算する金額 計",
            "cultivation_cost.added_to_acquisition_total",
        ),
        EtaxScalarField(
            "CULTIVATION",
            "APF02520",
            "成熟取得価額 計",
            "cultivation_cost.matured_acquisition_total",
        ),
        EtaxScalarField(
            "CULTIVATION",
            "APF02530",
            "翌年への繰越額 計",
            "cultivation_cost.carryover_to_next_total",
        ),
    ),
)


# ── 合成様式 (synthetic, 非公式・教育用) — 年度軸の外 ───────────────────────────────
#
# 実 様式 (KOA210) に取り違えないよう ``"synthetic"`` キーで隔離。data-driven 機構
# (path resolver / section 展開 / 整数円・桁数検証) を実 様式と独立に回し続けるための、
# 自由内訳ベースの最小様式。golden は pin しない (実様式が令和7年分の正)。

_SPEC_SYNTHETIC = EtaxFormatSpec(
    version="synthetic",
    form_id="青色申告決算書(一般用・合成)",
    items=(
        EtaxScalarField("PL", "PL010", "売上(収入)金額", "profit_and_loss.sales.subtotal"),
        EtaxScalarField("PL", "PL020", "売上原価", "profit_and_loss.cost_of_goods_sold.subtotal"),
        EtaxScalarField("PL", "PL030", "売上総利益", "profit_and_loss.gross_profit"),
        EtaxScalarField("PL", "PL040", "経費", "profit_and_loss.selling_admin_expenses.subtotal"),
        EtaxScalarField("PL", "PL050", "営業利益", "profit_and_loss.operating_income"),
        EtaxScalarField("PL", "PL080", "経常利益", "profit_and_loss.ordinary_income"),
        EtaxScalarField(
            "PL", "PL090", "青色申告特別控除前の所得金額", "profit_and_loss.net_income"
        ),
        EtaxScalarField("MONTHLY", "MN900", "売上(収入)金額 合計", "monthly.sales_total"),
        EtaxScalarField("MONTHLY", "MN910", "仕入金額 合計", "monthly.purchases_total"),
        EtaxScalarField("BS", "BS900", "資産合計", "balance_sheet.total_assets"),
        EtaxScalarField("BS", "BS910", "負債合計", "balance_sheet.total_liabilities"),
        EtaxScalarField("BS", "BS920", "純資産合計", "balance_sheet.total_equity"),
        EtaxSection(
            "MONTHLY",
            "MONTHLY_ROWS",
            "月別売上(収入)金額及び仕入金額",
            "monthly.rows",
            (
                EtaxSectionField("MN010", "月", "month", EtaxValueKind.MONTH),
                EtaxSectionField("MN011", "売上(収入)金額", "sales"),
                EtaxSectionField("MN012", "仕入金額", "purchases"),
            ),
        ),
        EtaxSection(
            "BS",
            "BS_ASSET_LINES",
            "資産の部 内訳",
            "balance_sheet.assets[].lines",
            (
                EtaxSectionField(
                    "BS010", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("BS011", "科目名", "name", _TEXT),
                EtaxSectionField("BS012", "金額", "balance"),
            ),
        ),
    ),
)


#: version → spec. 年度キー (令和7年分 = ``"2025"``) で 実 様式を切替。``"synthetic"`` は年度軸外。
#: Adding a 年度 registers one more entry here; nothing else changes.
ETAX_FORMAT_SPECS: dict[str, EtaxFormatSpec] = {
    _SPEC_2025.version: _SPEC_2025,
    _SPEC_2025_KOA220.version: _SPEC_2025_KOA220,
    _SPEC_2025_KOA240.version: _SPEC_2025_KOA240,
    _SPEC_SYNTHETIC.version: _SPEC_SYNTHETIC,
}

#: 既定の 様式 version — caller が pin しないときの最新の実様式 (令和7年分 一般用 KOA210).
LATEST_ETAX_VERSION = _SPEC_2025.version
#: 既定の KOA220(不動産所得用) / KOA240(農業所得用) version — 収入側 build 入口の既定 (#126).
LATEST_REAL_ESTATE_VERSION = _SPEC_2025_KOA220.version
LATEST_AGRICULTURAL_VERSION = _SPEC_2025_KOA240.version


def get_format_spec(version: str) -> EtaxFormatSpec:
    """Look up the e-Tax 様式 spec for ``version``; raise ``ValueError`` if unknown.

    The error lists the registered versions so a caller (or the MCP tool) gets a usable message
    rather than a bare ``KeyError``.
    """
    spec = ETAX_FORMAT_SPECS.get(version)
    if spec is None:
        known = ", ".join(sorted(ETAX_FORMAT_SPECS)) or "(none)"
        raise ValueError(f"unknown e-Tax format version {version!r}; known versions: {known}")
    return spec

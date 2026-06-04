"""Data-driven, versioned e-Tax 様式 specification — Issue #24.

The e-Tax 取込様式 changes by 年度 (項目の増減・桁数・コード体系), so the mapping from the
青色申告決算書 to e-Tax records is kept as **data**, not branching code: a :class:`EtaxFormatSpec`
is a list of field/section descriptors, each naming a *path* into the 決算書 snapshot
(:func:`ai_books.reports.financial_statements_snapshot`) plus the constraints that field must
satisfy. To support a new 年度 you register one more :class:`EtaxFormatSpec` in
:data:`ETAX_FORMAT_SPECS`; the export engine and the CSV/XML renderers never change.

Paths are dot-separated keys into the snapshot dict (``profit_and_loss.sales.subtotal``). A
segment ending in ``[]`` flattens a list and descends into each element, so one section spec can
gather 科目内訳 that live under several sub-sections (``balance_sheet.assets[].lines`` walks every
資産 区分's lines). :func:`resolve_scalar` reads a single value; :func:`resolve_list` reads a
(possibly flattened) list of row dicts.

The 2025 様式 here is a *synthetic* e-Tax-style layout (educational project; not the 国税庁 official
taxonomy) — faithful in spirit (面ごとの 項目コード, 金額の整数円・桁数制約, 科目内訳の繰り返し) so the
mapping/validation/golden machinery is exercised end to end without implying real filing compliance.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ai_books.models import EtaxValueKind

#: e-Tax 金額項目 の整数部桁数上限 (synthetic). A 金額 wider than this fails validation.
DEFAULT_MAX_INT_DIGITS = 13


class EtaxScalarField(NamedTuple):
    """One scalar e-Tax 項目 — a single value pulled from the 決算書 snapshot by ``source`` path.

    ``form`` is the 面 区分; ``item_code`` the e-Tax 項目コード; ``source`` a dot-path into the
    snapshot (no ``[]``). ``required`` decides whether a missing/empty value is a hard error;
    ``max_int_digits`` caps an ``AMOUNT``'s integer part.
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
    one per :class:`EtaxSectionField`, in spec order.
    """

    form: str
    section_code: str
    label: str
    source: str
    fields: tuple[EtaxSectionField, ...]


class EtaxFormatSpec(NamedTuple):
    """A whole versioned e-Tax 様式 — its scalar 項目 and repeating 内訳 sections.

    ``scalars`` are emitted first (in order), then each ``section``'s rows (in order). Swapping the
    spec is the *only* thing that changes when the 様式 changes for a new 年度.
    """

    version: str
    form_id: str
    scalars: tuple[EtaxScalarField, ...]
    sections: tuple[EtaxSection, ...]


# ── path resolution over the 決算書 snapshot ─────────────────────────────────────

#: Sentinel for "no value at this path" — distinct from a legitimately present ``None``.
MISSING: Any = object()


def _descend(node: Any, parts: list[str]) -> Any:
    """Walk ``parts`` into ``node``; ``[]``-suffixed segments flatten one list level.

    Returns :data:`MISSING` if any key is absent (or the shape is not walkable). When a ``[]``
    segment is present the result is a list (flattened one level for every trailing segment that
    itself yields a list), otherwise a single value.
    """
    if not parts:
        return node
    head, *rest = parts
    flatten = head.endswith("[]")
    key = head[:-2] if flatten else head
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


# ── 2025 様式 (synthetic) ─────────────────────────────────────────────────────────


_SPEC_2025 = EtaxFormatSpec(
    version="2025",
    form_id="青色申告決算書(一般用)",
    scalars=(
        # ── 1面 損益計算書 (段階表示) ──
        EtaxScalarField("PL", "PL010", "売上(収入)金額", "profit_and_loss.sales.subtotal"),
        EtaxScalarField("PL", "PL020", "売上原価", "profit_and_loss.cost_of_goods_sold.subtotal"),
        EtaxScalarField("PL", "PL030", "売上総利益", "profit_and_loss.gross_profit"),
        EtaxScalarField("PL", "PL040", "経費", "profit_and_loss.selling_admin_expenses.subtotal"),
        EtaxScalarField("PL", "PL050", "営業利益", "profit_and_loss.operating_income"),
        EtaxScalarField(
            "PL", "PL060", "営業外収益", "profit_and_loss.non_operating_income.subtotal"
        ),
        EtaxScalarField(
            "PL", "PL070", "営業外費用", "profit_and_loss.non_operating_expenses.subtotal"
        ),
        EtaxScalarField("PL", "PL080", "経常利益", "profit_and_loss.ordinary_income"),
        EtaxScalarField(
            "PL", "PL090", "青色申告特別控除前の所得金額", "profit_and_loss.net_income"
        ),
        # ── 2面 月別売上(収入)金額及び仕入金額 (合計) ──
        EtaxScalarField("MONTHLY", "MN900", "売上(収入)金額 合計", "monthly.sales_total"),
        EtaxScalarField("MONTHLY", "MN910", "仕入金額 合計", "monthly.purchases_total"),
        # ── 3面 減価償却費の計算 (合計) ──
        EtaxScalarField(
            "DEPRECIATION", "DP900", "本年分の償却費合計", "depreciation.total_depreciation"
        ),
        # ── 4面 製造原価の計算 ──
        EtaxScalarField(
            "MANUFACTURING", "MC010", "材料費", "manufacturing_cost.materials.subtotal"
        ),
        EtaxScalarField("MANUFACTURING", "MC020", "労務費", "manufacturing_cost.labor.subtotal"),
        EtaxScalarField(
            "MANUFACTURING", "MC030", "製造経費", "manufacturing_cost.overhead.subtotal"
        ),
        EtaxScalarField(
            "MANUFACTURING",
            "MC040",
            "当期製造費用",
            "manufacturing_cost.total_manufacturing_cost",
        ),
        EtaxScalarField(
            "MANUFACTURING",
            "MC050",
            "当期製品製造原価",
            "manufacturing_cost.cost_of_goods_manufactured",
        ),
        # ── 4面 貸借対照表 (合計) ──
        EtaxScalarField("BS", "BS900", "資産合計", "balance_sheet.total_assets"),
        EtaxScalarField("BS", "BS910", "負債合計", "balance_sheet.total_liabilities"),
        EtaxScalarField("BS", "BS920", "純資産合計", "balance_sheet.total_equity"),
        EtaxScalarField("BS", "BS930", "青色申告特別控除前所得金額", "balance_sheet.net_income"),
    ),
    sections=(
        # ── 1面 売上原価 内訳 (科目別) ──
        EtaxSection(
            "PL",
            "PL_COGS_LINES",
            "売上原価 内訳",
            "profit_and_loss.cost_of_goods_sold.lines",
            (
                EtaxSectionField(
                    "PL110", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("PL111", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("PL112", "金額", "amount", EtaxValueKind.AMOUNT),
            ),
        ),
        # ── 1面 経費 内訳 (科目別) ──
        EtaxSection(
            "PL",
            "PL_SGA_LINES",
            "経費 内訳",
            "profit_and_loss.selling_admin_expenses.lines",
            (
                EtaxSectionField(
                    "PL120", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("PL121", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("PL122", "金額", "amount", EtaxValueKind.AMOUNT),
            ),
        ),
        # ── 2面 月別売上(収入)金額及び仕入金額 (12行) ──
        EtaxSection(
            "MONTHLY",
            "MONTHLY_ROWS",
            "月別売上(収入)金額及び仕入金額",
            "monthly.rows",
            (
                EtaxSectionField("MN010", "月", "month", EtaxValueKind.MONTH),
                EtaxSectionField("MN011", "売上(収入)金額", "sales", EtaxValueKind.AMOUNT),
                EtaxSectionField("MN012", "仕入金額", "purchases", EtaxValueKind.AMOUNT),
            ),
        ),
        # ── 3面 減価償却費の計算 (科目別) ──
        EtaxSection(
            "DEPRECIATION",
            "DEPRECIATION_LINES",
            "減価償却費の計算",
            "depreciation.lines",
            (
                EtaxSectionField(
                    "DP010", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("DP011", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("DP012", "取得価額", "acquisition_cost", EtaxValueKind.AMOUNT),
                EtaxSectionField(
                    "DP013", "本年分の償却費", "depreciation_expense", EtaxValueKind.AMOUNT
                ),
                EtaxSectionField(
                    "DP014", "期末未償却残高", "closing_book_value", EtaxValueKind.AMOUNT
                ),
            ),
        ),
        # ── 4面 貸借対照表 資産の部 内訳 (全区分の科目を flatten) ──
        EtaxSection(
            "BS",
            "BS_ASSET_LINES",
            "資産の部 内訳",
            "balance_sheet.assets[].lines",
            (
                EtaxSectionField(
                    "BS010", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("BS011", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("BS012", "金額", "balance", EtaxValueKind.AMOUNT),
            ),
        ),
        # ── 4面 貸借対照表 負債の部 内訳 ──
        EtaxSection(
            "BS",
            "BS_LIABILITY_LINES",
            "負債の部 内訳",
            "balance_sheet.liabilities[].lines",
            (
                EtaxSectionField(
                    "BS020", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("BS021", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("BS022", "金額", "balance", EtaxValueKind.AMOUNT),
            ),
        ),
        # ── 4面 貸借対照表 純資産の部 内訳 ──
        EtaxSection(
            "BS",
            "BS_EQUITY_LINES",
            "純資産の部 内訳",
            "balance_sheet.equity[].lines",
            (
                EtaxSectionField(
                    "BS030", "科目コード", "code", EtaxValueKind.CODE, is_account_code=True
                ),
                EtaxSectionField("BS031", "科目名", "name", EtaxValueKind.TEXT),
                EtaxSectionField("BS032", "金額", "balance", EtaxValueKind.AMOUNT),
            ),
        ),
    ),
)


#: version → spec. Adding a 年度 registers one more entry here; nothing else changes.
ETAX_FORMAT_SPECS: dict[str, EtaxFormatSpec] = {_SPEC_2025.version: _SPEC_2025}

#: The newest 様式 version — the default when a caller does not pin one.
LATEST_ETAX_VERSION = _SPEC_2025.version


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

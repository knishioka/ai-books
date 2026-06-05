/**
 * Data-driven, versioned e-Tax 様式 specification — the TS twin of `ai_books.etax.spec`.
 *
 * The e-Tax 取込様式 changes by 年度 (項目の増減・桁数・コード体系), so the mapping from the
 * 青色申告決算書 to e-Tax records is kept as **data**, not branching code: a {@link EtaxFormatSpec}
 * is an ordered list of field/section descriptors, each naming a *path* into the 決算書 snapshot
 * plus the constraints that field must satisfy. To support a new 年度, register one more spec in
 * {@link ETAX_FORMAT_SPECS}; the export engine and the CSV/XML renderers never change.
 *
 * Paths are dot-separated keys into the snapshot. A numeric segment indexes a list
 * (`monthly.rows.0.sales`); a segment ending in `[]` flattens a list and descends into each
 * element (`balance_sheet.assets[].lines` walks every 資産 区分's lines).
 *
 * The 2025 spec maps onto the **official 所得税関係 XML 様式** KOA210 (一般用, v11.0) — see the Python
 * twin's module docstring and `docs/etax/` (#76) for the field catalog and structural deltas. The
 * earlier *synthetic* (非公式・教育用) layout is kept off the 年度 axis under the `"synthetic"`
 * version key so its machinery still runs without being mistaken for the real 様式.
 */

export type EtaxValueKind = "amount" | "code" | "month" | "text";

/** e-Tax 金額項目 の整数部桁数上限 (KOA210 金額の標準書式 = 整数13桁). */
export const DEFAULT_MAX_INT_DIGITS = 13;

export interface EtaxScalarField {
  descriptor: "scalar";
  form: string;
  itemCode: string;
  label: string;
  /** Dot-path into the snapshot (numeric segments index lists; no `[]`). */
  source: string;
  kind: EtaxValueKind;
  required: boolean;
  maxIntDigits: number;
}

export interface EtaxSectionField {
  itemCode: string;
  label: string;
  /** Path relative to each row of the section's list. */
  source: string;
  kind: EtaxValueKind;
  required: boolean;
  maxIntDigits: number;
  isAccountCode: boolean;
}

export interface EtaxSection {
  descriptor: "section";
  form: string;
  sectionCode: string;
  label: string;
  /** Path to a list in the snapshot, optionally using `[]` to flatten nested lists. */
  source: string;
  fields: EtaxSectionField[];
}

/** One fixed 勘定科目行 of the real 様式 — `accountCodes` route (summed) into `itemCode`. */
export interface EtaxFixedRow {
  itemCode: string;
  label: string;
  accountCodes: string[];
  /** Adjust the stored amount before summing: `abs` for 棚卸高 stored as a contra, `neg` to flip. */
  sign: "as_is" | "abs" | "neg";
}

/**
 * The real 様式's *fixed 勘定科目行* block (#78) — route snapshot `lines` by コード into a fixed set
 * of 項目コード; 科目 with no fixed row spill into the 追加科目枠 (`overflow`):
 * `slots` fills `overflowMax` rep slots (exhaustion ⇒ 未分類 error), `accumulate` sums all
 * unmatched into one `overflowCode`, `drop` discards them silently (#83 営業外橋渡し — 帳簿上は
 * 分類済みだが当該様式に枠が無い 科目).
 */
export interface EtaxFixedSection {
  descriptor: "fixed";
  form: string;
  sectionCode: string;
  label: string;
  sources: string[];
  valueField: string;
  rows: EtaxFixedRow[];
  overflowCode: string | null;
  overflowLabel: string;
  overflowMax: number;
  overflowMode: "slots" | "accumulate" | "drop";
  kind: EtaxValueKind;
  maxIntDigits: number;
}

/**
 * A scalar 項目 whose value is a base snapshot 金額 adjusted by other sections' routed totals (#83).
 * KOA210 files 利子割引料 under 経費, but our chart classifies it as 営業外費用 — so 経費計(AMF00380)
 * reads 販管費 小計 *plus* the homed 営業外費用 the bridge {@link EtaxFixedSection} routes into 経費,
 * and 差引金額２(AMF00390) the same base *minus* it. `addSections` / `subSections` name the
 * `sectionCode` of **earlier** fixed sections whose routed total is added / subtracted (absent ⇒ 0).
 */
export interface EtaxComputedField {
  descriptor: "computed";
  form: string;
  itemCode: string;
  label: string;
  baseSource: string;
  addSections: string[];
  subSections: string[];
  kind: EtaxValueKind;
  required: boolean;
  maxIntDigits: number;
}

export type EtaxItem =
  | EtaxScalarField
  | EtaxSection
  | EtaxFixedSection
  | EtaxComputedField;

export interface EtaxFormatSpec {
  version: string;
  formId: string;
  items: EtaxItem[];
}

/** Sentinel for "no value at this path" — distinct from a legitimately present `null`. */
export const MISSING = Symbol("etax.missing");

function scalar(
  form: string,
  itemCode: string,
  label: string,
  source: string,
  kind: EtaxValueKind = "amount",
  required = true,
  maxIntDigits = DEFAULT_MAX_INT_DIGITS,
): EtaxScalarField {
  return {
    descriptor: "scalar",
    form,
    itemCode,
    label,
    source,
    kind,
    required,
    maxIntDigits,
  };
}

function field(
  itemCode: string,
  label: string,
  source: string,
  kind: EtaxValueKind = "amount",
  options: {
    required?: boolean;
    maxIntDigits?: number;
    isAccountCode?: boolean;
  } = {},
): EtaxSectionField {
  return {
    itemCode,
    label,
    source,
    kind,
    required: options.required ?? true,
    maxIntDigits: options.maxIntDigits ?? DEFAULT_MAX_INT_DIGITS,
    isAccountCode: options.isAccountCode ?? false,
  };
}

function fixedRow(
  itemCode: string,
  label: string,
  accountCodes: string[],
  sign: EtaxFixedRow["sign"] = "as_is",
): EtaxFixedRow {
  return { itemCode, label, accountCodes, sign };
}

function fixedSection(
  form: string,
  sectionCode: string,
  label: string,
  sources: string[],
  valueField: string,
  rows: EtaxFixedRow[],
  overflow: {
    code?: string | null;
    label?: string;
    max?: number;
    mode?: EtaxFixedSection["overflowMode"];
  } = {},
): EtaxFixedSection {
  return {
    descriptor: "fixed",
    form,
    sectionCode,
    label,
    sources,
    valueField,
    rows,
    overflowCode: overflow.code ?? null,
    overflowLabel: overflow.label ?? "追加科目",
    overflowMax: overflow.max ?? 0,
    overflowMode: overflow.mode ?? "slots",
    kind: "amount",
    maxIntDigits: DEFAULT_MAX_INT_DIGITS,
  };
}

function computed(
  form: string,
  itemCode: string,
  label: string,
  baseSource: string,
  sections: { add?: string[]; sub?: string[] } = {},
): EtaxComputedField {
  return {
    descriptor: "computed",
    form,
    itemCode,
    label,
    baseSource,
    addSections: sections.add ?? [],
    subSections: sections.sub ?? [],
    kind: "amount",
    required: true,
    maxIntDigits: DEFAULT_MAX_INT_DIGITS,
  };
}

// ── path resolution over the 決算書 snapshot ─────────────────────────────────────

type Json = unknown;

function descend(node: Json, parts: string[]): Json | typeof MISSING {
  if (parts.length === 0) return node;
  const [head, ...rest] = parts;
  const flatten = head.endsWith("[]");
  const key = flatten ? head.slice(0, -2) : head;
  if (!flatten && Array.isArray(node)) {
    if (!/^\d+$/.test(key)) return MISSING;
    const index = Number(key);
    if (index >= node.length) return MISSING;
    return descend(node[index], rest);
  }
  if (
    typeof node !== "object" ||
    node === null ||
    Array.isArray(node) ||
    !(key in node)
  ) {
    return MISSING;
  }
  const value = (node as Record<string, Json>)[key];
  if (!flatten) return descend(value, rest);
  if (!Array.isArray(value)) return MISSING;
  const collected: Json[] = [];
  for (const item of value) {
    const descended = descend(item, rest);
    if (descended === MISSING) return MISSING;
    if (rest.length > 0 && Array.isArray(descended)) {
      collected.push(...descended);
    } else {
      collected.push(descended);
    }
  }
  return collected;
}

/** Resolve a scalar `path` (no `[]`) into the snapshot; {@link MISSING} if absent. */
export function resolveScalar(
  snapshot: Json,
  path: string,
): Json | typeof MISSING {
  return descend(snapshot, path.split("."));
}

/** Resolve a list `path` (the section source) into a list of row dicts (`[]` if absent). */
export function resolveList(
  snapshot: Json,
  path: string,
): Record<string, Json>[] {
  const value = descend(snapshot, path.split("."));
  if (value === MISSING || !Array.isArray(value)) return [];
  return value.filter(
    (row): row is Record<string, Json> =>
      typeof row === "object" && row !== null && !Array.isArray(row),
  );
}

// ── 令和7年分 (2025) — 実 様式 KOA210 青色申告決算書(一般用) v11.0 ───────────────────

/** 1月..12月 の固定 項目コード (AMF00600/00610 …, +30 per month) ← `monthly.rows[i]`. */
const MONTHLY_FIELDS: EtaxScalarField[] = Array.from(
  { length: 12 },
  (_unused, i): EtaxScalarField[] => {
    const month = i + 1;
    const base = 600 + i * 30;
    const code = (n: number) => `AMF${String(n).padStart(5, "0")}`;
    return [
      scalar(
        "MONTHLY",
        code(base),
        `${month}月 売上(収入)金額`,
        `monthly.rows.${i}.sales`,
        "amount",
        false,
      ),
      scalar(
        "MONTHLY",
        code(base + 10),
        `${month}月 仕入金額`,
        `monthly.rows.${i}.purchases`,
        "amount",
        false,
      ),
    ];
  },
).flat();

/** 経費 固定行 — 勘定科目コード (7xxx) → KOA210 経費 項目コード (AMF00190-00370). */
const EXPENSE_ROWS: EtaxFixedRow[] = [
  fixedRow("AMF00190", "租税公課", ["7110"]),
  fixedRow("AMF00200", "荷造運賃", ["7120"]),
  fixedRow("AMF00210", "水道光熱費", ["7130"]),
  fixedRow("AMF00220", "旅費交通費", ["7140"]),
  fixedRow("AMF00230", "通信費", ["7150"]),
  fixedRow("AMF00240", "広告宣伝費", ["7160"]),
  fixedRow("AMF00250", "接待交際費", ["7170"]),
  fixedRow("AMF00260", "損害保険料", ["7180"]),
  fixedRow("AMF00270", "修繕費", ["7190"]),
  fixedRow("AMF00280", "消耗品費", ["7200"]),
  fixedRow("AMF00290", "減価償却費", ["7210"]),
  fixedRow("AMF00300", "福利厚生費", ["7220"]),
  fixedRow("AMF00310", "給料賃金", ["7230"]),
  fixedRow("AMF00320", "外注工賃", ["7240"]),
  fixedRow("AMF00340", "地代家賃", ["7250"]),
  fixedRow("AMF00370", "雑費", ["7290"]),
];

/**
 * 営業外費用 のうち 様式に居場所がある 科目 → KOA210 経費 項目コード (#83 営業外マッピング方針)。
 * 帳簿では 営業外費用 (8xxx) だが KOA210(一般用) は 利子割引料 を 経費 AMF00330 に置く。橋渡しは
 * `overflowMode="drop"` の fixedSection で行い、ここに無い 営業外 (雑損失 等) は drop。
 */
const NON_OPERATING_EXPENSE_ROWS: EtaxFixedRow[] = [
  fixedRow("AMF00330", "利子割引料", ["8210"]),
];

/** 資産の部(期末) 固定行 — 勘定科目コード (1xxx) → KOA210 資産 項目コード (AMG00260-00430). */
const ASSET_ROWS: EtaxFixedRow[] = [
  fixedRow("AMG00260", "現金", ["1110"]),
  fixedRow("AMG00270", "当座預金", ["1130"]),
  fixedRow("AMG00280", "定期預金", ["1142"]),
  fixedRow("AMG00290", "その他の預金", ["1140", "1141"]),
  fixedRow("AMG00300", "受取手形", ["1150"]),
  fixedRow("AMG00310", "売掛金", ["1160"]),
  fixedRow("AMG00320", "有価証券", ["1170"]),
  fixedRow("AMG00330", "棚卸資産", ["1180"]),
  fixedRow("AMG00340", "前払金", ["1190"]),
  fixedRow("AMG00350", "貸付金", ["1200"]),
  fixedRow("AMG00360", "建物", ["1510"]),
  fixedRow("AMG00370", "建物附属設備", ["1520"]),
  fixedRow("AMG00380", "機械装置", ["1530"]),
  fixedRow("AMG00390", "車両運搬具", ["1540"]),
  fixedRow("AMG00400", "工具・器具・備品", ["1550"]),
  fixedRow("AMG00410", "土地", ["1560"]),
  fixedRow("AMG00430", "事業主貸", ["1290"]),
];

/** 負債・資本の部(期末) 固定行 — 負債(2xxx) と 純資産(3xxx) を統合 (借入金 = 短期+長期 を合算). */
const LIABILITY_EQUITY_ROWS: EtaxFixedRow[] = [
  fixedRow("AMG00640", "支払手形", ["2110"]),
  fixedRow("AMG00650", "買掛金", ["2120"]),
  fixedRow("AMG00660", "借入金", ["2160", "2510"]),
  fixedRow("AMG00670", "未払金", ["2130"]),
  fixedRow("AMG00680", "前受金", ["2140"]),
  fixedRow("AMG00690", "預り金", ["2150"]),
  fixedRow("AMG00730", "事業主借", ["3120"]),
  fixedRow("AMG00740", "元入金", ["3110"]),
];

/** 製造原価 その他経費 固定行 — 製造間接費(63xx) → KOA210 製造原価 項目コード (AMH00100-00140). */
const MANUFACTURING_OVERHEAD_ROWS: EtaxFixedRow[] = [
  fixedRow("AMH00100", "外注工賃", ["6310"]),
  fixedRow("AMH00110", "電力費", ["6320"]),
  fixedRow("AMH00130", "修繕費", ["6340"]),
  fixedRow("AMH00140", "減価償却費", ["6330"]),
];

const SPEC_2025: EtaxFormatSpec = {
  version: "2025",
  formId: "青色申告決算書(一般用)",
  items: [
    // ── 損益計算書 ──
    scalar(
      "PL",
      "AMF00100",
      "売上(収入)金額",
      "profit_and_loss.sales.subtotal",
    ),
    // 売上原価: 期首 + 仕入(商品仕入+製造原価内訳の合算) - 期末 = 差引原価.
    fixedSection(
      "PL",
      "PL_COGS",
      "売上原価",
      ["profit_and_loss.cost_of_goods_sold.lines"],
      "amount",
      [
        fixedRow("AMF00120", "期首商品(製品)棚卸高", ["5110", "6110"]),
        fixedRow("AMF00150", "期末商品(製品)棚卸高", ["5130", "6130"], "abs"),
      ],
      {
        code: "AMF00130",
        label: "仕入金額(製品製造原価)",
        mode: "accumulate",
      },
    ),
    scalar(
      "PL",
      "AMF00160",
      "差引原価",
      "profit_and_loss.cost_of_goods_sold.subtotal",
    ),
    scalar("PL", "AMF00170", "差引金額１", "profit_and_loss.gross_profit"),
    // 経費: 固定勘定科目行 (AMF00190-00370) + 追加科目枠 AMF00360 (rep=6).
    fixedSection(
      "PL",
      "PL_EXPENSES",
      "経費",
      ["profit_and_loss.selling_admin_expenses.lines"],
      "amount",
      EXPENSE_ROWS,
      { code: "AMF00360", label: "追加科目の金額", max: 6 },
    ),
    // 営業外費用→経費 橋渡し: 利子割引料(8210) を 経費 AMF00330 へ; 居場所の無い 営業外 は drop (#83).
    fixedSection(
      "PL",
      "PL_NON_OP_EXPENSES",
      "営業外費用(様式上は経費)",
      ["profit_and_loss.non_operating_expenses.lines"],
      "amount",
      NON_OPERATING_EXPENSE_ROWS,
      { mode: "drop" },
    ),
    // 経費計 = 販管費小計 + 橋渡しした 営業外費用 (利子割引料) → 経費行合計と一致.
    computed(
      "PL",
      "AMF00380",
      "経費 計",
      "profit_and_loss.selling_admin_expenses.subtotal",
      { add: ["PL_NON_OP_EXPENSES"] },
    ),
    // 差引金額２ = 営業利益 - 橋渡しした 営業外費用 → 差引金額１ - 経費計 と一致.
    computed(
      "PL",
      "AMF00390",
      "差引金額２",
      "profit_and_loss.operating_income",
      { sub: ["PL_NON_OP_EXPENSES"] },
    ),
    scalar(
      "PL",
      "AMF00500",
      "青色申告特別控除前の所得金額",
      "profit_and_loss.net_income",
    ),
    // ── 月別売上(収入)金額及び仕入金額 ──
    ...MONTHLY_FIELDS,
    scalar(
      "MONTHLY",
      "AMF00980",
      "月別売上(収入)金額(計)",
      "monthly.sales_total",
    ),
    scalar(
      "MONTHLY",
      "AMF00990",
      "月別仕入金額(計)",
      "monthly.purchases_total",
    ),
    // ── 減価償却費の計算 (繰返しブロック AMF016xx) ──
    {
      descriptor: "section",
      form: "DEPRECIATION",
      sectionCode: "DEPRECIATION_LINES",
      label: "減価償却費の計算",
      source: "depreciation.lines",
      fields: [
        field("AMF01610", "減価償却資産の名称等", "name", "text"),
        field("AMF01640", "取得価額", "acquisition_cost", "amount"),
        field(
          "AMF01750",
          "本年分の償却費合計",
          "depreciation_expense",
          "amount",
        ),
        field("AMF01780", "未償却残高", "closing_book_value", "amount"),
      ],
    },
    scalar(
      "DEPRECIATION",
      "AMF01830",
      "本年分の償却費合計(計)",
      "depreciation.total_depreciation",
    ),
    scalar(
      "DEPRECIATION",
      "AMF01840",
      "本年分の必要経費算入額(計)",
      "depreciation.expense_total",
      "amount",
      false,
    ),
    // ── 製造原価の計算 (KOA210 内包, AMH*) ──
    fixedSection(
      "MANUFACTURING",
      "MC_MATERIALS",
      "原材料費",
      ["manufacturing_cost.materials.lines"],
      "amount",
      [
        fixedRow("AMH00030", "期首原材料棚卸高", ["6110"]),
        fixedRow("AMH00060", "期末原材料棚卸高", ["6130"], "abs"),
      ],
      { code: "AMH00040", label: "原材料仕入高", mode: "accumulate" },
    ),
    scalar(
      "MANUFACTURING",
      "AMH00070",
      "差引原材料費",
      "manufacturing_cost.materials.subtotal",
    ),
    scalar(
      "MANUFACTURING",
      "AMH00080",
      "労務費",
      "manufacturing_cost.labor.subtotal",
    ),
    fixedSection(
      "MANUFACTURING",
      "MC_OVERHEAD",
      "その他の製造経費",
      ["manufacturing_cost.overhead.lines"],
      "amount",
      MANUFACTURING_OVERHEAD_ROWS,
      { code: "AMH00150", label: "追加科目の金額", max: 8 },
    ),
    scalar(
      "MANUFACTURING",
      "AMH00170",
      "その他の製造経費 計",
      "manufacturing_cost.overhead.subtotal",
    ),
    scalar(
      "MANUFACTURING",
      "AMH00180",
      "総製造費",
      "manufacturing_cost.total_manufacturing_cost",
    ),
    scalar(
      "MANUFACTURING",
      "AMH00220",
      "製品製造原価",
      "manufacturing_cost.cost_of_goods_manufactured",
    ),
    // ── 貸借対照表 (期末) ──
    fixedSection(
      "BS",
      "BS_ASSETS",
      "資産の部(期末)",
      ["balance_sheet.assets[].lines"],
      "balance",
      ASSET_ROWS,
      { code: "AMG00420", label: "追加科目の金額", max: 7 },
    ),
    scalar(
      "BS",
      "AMG00440",
      "資産の部(期末)合計",
      "balance_sheet.total_assets",
    ),
    fixedSection(
      "BS",
      "BS_LIABILITIES_EQUITY",
      "負債・資本の部(期末)",
      ["balance_sheet.liabilities[].lines", "balance_sheet.equity[].lines"],
      "balance",
      LIABILITY_EQUITY_ROWS,
      { code: "AMG00700", label: "追加科目の金額", max: 7 },
    ),
    scalar(
      "BS",
      "AMG00750",
      "青色申告特別控除前の所得金額",
      "balance_sheet.net_income",
    ),
    scalar(
      "BS",
      "AMG00760",
      "負債・資本の部(期末)合計",
      "balance_sheet.total_assets",
    ),
  ],
};

// ── 合成様式 (synthetic, 非公式・教育用) — 年度軸の外 ───────────────────────────────

const SPEC_SYNTHETIC: EtaxFormatSpec = {
  version: "synthetic",
  formId: "青色申告決算書(一般用・合成)",
  items: [
    scalar("PL", "PL010", "売上(収入)金額", "profit_and_loss.sales.subtotal"),
    scalar(
      "PL",
      "PL020",
      "売上原価",
      "profit_and_loss.cost_of_goods_sold.subtotal",
    ),
    scalar("PL", "PL030", "売上総利益", "profit_and_loss.gross_profit"),
    scalar(
      "PL",
      "PL040",
      "経費",
      "profit_and_loss.selling_admin_expenses.subtotal",
    ),
    scalar("PL", "PL050", "営業利益", "profit_and_loss.operating_income"),
    scalar("PL", "PL080", "経常利益", "profit_and_loss.ordinary_income"),
    scalar(
      "PL",
      "PL090",
      "青色申告特別控除前の所得金額",
      "profit_and_loss.net_income",
    ),
    scalar("MONTHLY", "MN900", "売上(収入)金額 合計", "monthly.sales_total"),
    scalar("MONTHLY", "MN910", "仕入金額 合計", "monthly.purchases_total"),
    scalar("BS", "BS900", "資産合計", "balance_sheet.total_assets"),
    scalar("BS", "BS910", "負債合計", "balance_sheet.total_liabilities"),
    scalar("BS", "BS920", "純資産合計", "balance_sheet.total_equity"),
    {
      descriptor: "section",
      form: "MONTHLY",
      sectionCode: "MONTHLY_ROWS",
      label: "月別売上(収入)金額及び仕入金額",
      source: "monthly.rows",
      fields: [
        field("MN010", "月", "month", "month"),
        field("MN011", "売上(収入)金額", "sales", "amount"),
        field("MN012", "仕入金額", "purchases", "amount"),
      ],
    },
    {
      descriptor: "section",
      form: "BS",
      sectionCode: "BS_ASSET_LINES",
      label: "資産の部 内訳",
      source: "balance_sheet.assets[].lines",
      fields: [
        field("BS010", "科目コード", "code", "code", { isAccountCode: true }),
        field("BS011", "科目名", "name", "text"),
        field("BS012", "金額", "balance", "amount"),
      ],
    },
  ],
};

/** version → spec. 年度キー ("2025") で実様式を切替; "synthetic" は年度軸外. */
export const ETAX_FORMAT_SPECS: Record<string, EtaxFormatSpec> = {
  [SPEC_2025.version]: SPEC_2025,
  [SPEC_SYNTHETIC.version]: SPEC_SYNTHETIC,
};

/** The newest 様式 version — the default when a caller does not pin one (令和7年分). */
export const LATEST_ETAX_VERSION = SPEC_2025.version;

/** Look up the e-Tax 様式 spec for `version`; throw if unknown. */
export function getFormatSpec(version: string): EtaxFormatSpec {
  const spec = ETAX_FORMAT_SPECS[version];
  if (!spec) {
    const known = Object.keys(ETAX_FORMAT_SPECS).sort().join(", ") || "(none)";
    throw new Error(
      `unknown e-Tax format version ${JSON.stringify(version)}; known versions: ${known}`,
    );
  }
  return spec;
}

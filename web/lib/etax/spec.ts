/**
 * Data-driven, versioned e-Tax 様式 specification — the TS twin of `ai_books.etax.spec`.
 *
 * The e-Tax 取込様式 changes by 年度 (項目の増減・桁数・コード体系), so the mapping from the
 * 青色申告決算書 to e-Tax records is kept as **data**, not branching code: a {@link EtaxFormatSpec}
 * is a list of field/section descriptors, each naming a *path* into the 決算書 snapshot plus the
 * constraints that field must satisfy. To support a new 年度, register one more spec in
 * {@link ETAX_FORMAT_SPECS}; the export engine and the CSV/XML renderers never change.
 *
 * Paths are dot-separated keys into the snapshot. A segment ending in `[]` flattens a list and
 * descends into each element, so one section spec can gather 科目内訳 that live under several
 * sub-sections (`balance_sheet.assets[].lines` walks every 資産 区分's lines).
 *
 * The 2025 様式 here is a *synthetic* e-Tax-style layout (educational project; not the 国税庁
 * official taxonomy), faithful in spirit so the mapping/validation/golden machinery is exercised
 * end to end without implying real filing compliance.
 */

export type EtaxValueKind = "amount" | "code" | "month" | "text";

/** e-Tax 金額項目 の整数部桁数上限 (synthetic). */
export const DEFAULT_MAX_INT_DIGITS = 13;

export interface EtaxScalarField {
  form: string;
  itemCode: string;
  label: string;
  /** Dot-path into the snapshot (no `[]`). */
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
  form: string;
  sectionCode: string;
  label: string;
  /** Path to a list in the snapshot, optionally using `[]` to flatten nested lists. */
  source: string;
  fields: EtaxSectionField[];
}

export interface EtaxFormatSpec {
  version: string;
  formId: string;
  scalars: EtaxScalarField[];
  sections: EtaxSection[];
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
  return { form, itemCode, label, source, kind, required, maxIntDigits };
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

// ── path resolution over the 決算書 snapshot ─────────────────────────────────────

type Json = unknown;

function descend(node: Json, parts: string[]): Json | typeof MISSING {
  if (parts.length === 0) return node;
  const [head, ...rest] = parts;
  const flatten = head.endsWith("[]");
  const key = flatten ? head.slice(0, -2) : head;
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

// ── 2025 様式 (synthetic) ─────────────────────────────────────────────────────────

const SPEC_2025: EtaxFormatSpec = {
  version: "2025",
  formId: "青色申告決算書(一般用)",
  scalars: [
    // ── 1面 損益計算書 (段階表示) ──
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
    scalar(
      "PL",
      "PL060",
      "営業外収益",
      "profit_and_loss.non_operating_income.subtotal",
    ),
    scalar(
      "PL",
      "PL070",
      "営業外費用",
      "profit_and_loss.non_operating_expenses.subtotal",
    ),
    scalar("PL", "PL080", "経常利益", "profit_and_loss.ordinary_income"),
    scalar(
      "PL",
      "PL090",
      "青色申告特別控除前の所得金額",
      "profit_and_loss.net_income",
    ),
    // ── 2面 月別売上(収入)金額及び仕入金額 (合計) ──
    scalar("MONTHLY", "MN900", "売上(収入)金額 合計", "monthly.sales_total"),
    scalar("MONTHLY", "MN910", "仕入金額 合計", "monthly.purchases_total"),
    // ── 3面 減価償却費の計算 (合計) ──
    scalar(
      "DEPRECIATION",
      "DP900",
      "本年分の償却費合計",
      "depreciation.total_depreciation",
    ),
    // ── 4面 製造原価の計算 ──
    scalar(
      "MANUFACTURING",
      "MC010",
      "材料費",
      "manufacturing_cost.materials.subtotal",
    ),
    scalar(
      "MANUFACTURING",
      "MC020",
      "労務費",
      "manufacturing_cost.labor.subtotal",
    ),
    scalar(
      "MANUFACTURING",
      "MC030",
      "製造経費",
      "manufacturing_cost.overhead.subtotal",
    ),
    scalar(
      "MANUFACTURING",
      "MC040",
      "当期製造費用",
      "manufacturing_cost.total_manufacturing_cost",
    ),
    scalar(
      "MANUFACTURING",
      "MC050",
      "当期製品製造原価",
      "manufacturing_cost.cost_of_goods_manufactured",
    ),
    // ── 4面 貸借対照表 (合計) ──
    scalar("BS", "BS900", "資産合計", "balance_sheet.total_assets"),
    scalar("BS", "BS910", "負債合計", "balance_sheet.total_liabilities"),
    scalar("BS", "BS920", "純資産合計", "balance_sheet.total_equity"),
    scalar(
      "BS",
      "BS930",
      "青色申告特別控除前所得金額",
      "balance_sheet.net_income",
    ),
  ],
  sections: [
    // ── 1面 売上原価 内訳 (科目別) ──
    {
      form: "PL",
      sectionCode: "PL_COGS_LINES",
      label: "売上原価 内訳",
      source: "profit_and_loss.cost_of_goods_sold.lines",
      fields: [
        field("PL110", "科目コード", "code", "code", { isAccountCode: true }),
        field("PL111", "科目名", "name", "text"),
        field("PL112", "金額", "amount", "amount"),
      ],
    },
    // ── 1面 経費 内訳 (科目別) ──
    {
      form: "PL",
      sectionCode: "PL_SGA_LINES",
      label: "経費 内訳",
      source: "profit_and_loss.selling_admin_expenses.lines",
      fields: [
        field("PL120", "科目コード", "code", "code", { isAccountCode: true }),
        field("PL121", "科目名", "name", "text"),
        field("PL122", "金額", "amount", "amount"),
      ],
    },
    // ── 2面 月別売上(収入)金額及び仕入金額 (12行) ──
    {
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
    // ── 3面 減価償却費の計算 (科目別) ──
    {
      form: "DEPRECIATION",
      sectionCode: "DEPRECIATION_LINES",
      label: "減価償却費の計算",
      source: "depreciation.lines",
      fields: [
        field("DP010", "科目コード", "code", "code", { isAccountCode: true }),
        field("DP011", "科目名", "name", "text"),
        field("DP012", "取得価額", "acquisition_cost", "amount"),
        field("DP013", "本年分の償却費", "depreciation_expense", "amount"),
        field("DP014", "期末未償却残高", "closing_book_value", "amount"),
      ],
    },
    // ── 4面 貸借対照表 資産の部 内訳 (全区分の科目を flatten) ──
    {
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
    // ── 4面 貸借対照表 負債の部 内訳 ──
    {
      form: "BS",
      sectionCode: "BS_LIABILITY_LINES",
      label: "負債の部 内訳",
      source: "balance_sheet.liabilities[].lines",
      fields: [
        field("BS020", "科目コード", "code", "code", { isAccountCode: true }),
        field("BS021", "科目名", "name", "text"),
        field("BS022", "金額", "balance", "amount"),
      ],
    },
    // ── 4面 貸借対照表 純資産の部 内訳 ──
    {
      form: "BS",
      sectionCode: "BS_EQUITY_LINES",
      label: "純資産の部 内訳",
      source: "balance_sheet.equity[].lines",
      fields: [
        field("BS030", "科目コード", "code", "code", { isAccountCode: true }),
        field("BS031", "科目名", "name", "text"),
        field("BS032", "金額", "balance", "amount"),
      ],
    },
  ],
};

/** version → spec. Adding a 年度 registers one more entry here; nothing else changes. */
export const ETAX_FORMAT_SPECS: Record<string, EtaxFormatSpec> = {
  [SPEC_2025.version]: SPEC_2025,
};

/** The newest 様式 version — the default when a caller does not pin one. */
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

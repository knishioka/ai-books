/**
 * Enum string unions and statement-layout metadata, mirroring `ai_books.models.enums` and the
 * grouping tables in `ai_books.aggregation`. These are the values stored verbatim in Postgres
 * (so a row round-trips without translation) plus the ordered 段階表示 / 表示区分 layout the P/L,
 * B/S and 製造原価 reports roll up under.
 */

export type AccountType =
  | "asset"
  | "liability"
  | "equity"
  | "revenue"
  | "expense";

export type NormalSide = "debit" | "credit";

export type EntrySide = "debit" | "credit";

export type EntryStatus = "draft" | "posted" | "voided";

export type StatementCategory =
  // 損益計算書 (P/L)
  | "sales"
  | "cost_of_goods_sold"
  | "selling_admin_expenses"
  | "non_operating_income"
  | "non_operating_expenses"
  // 製造原価の計算 (#23)
  | "manufacturing_materials"
  | "manufacturing_labor"
  | "manufacturing_overhead"
  // 貸借対照表 (B/S)
  | "current_assets"
  | "fixed_assets"
  | "current_liabilities"
  | "fixed_liabilities"
  | "equity";

/** Entries whose `source` marks them a 期末整理仕訳 (the 修正記入 columns of the 精算表). */
export const YEAR_END_ADJUSTMENT_SOURCE = "year_end_adjustment";

/** The account type each 表示区分 must belong to (mirrors `STATEMENT_CATEGORY_ACCOUNT_TYPE`). */
export const STATEMENT_CATEGORY_ACCOUNT_TYPE: Record<
  StatementCategory,
  AccountType
> = {
  sales: "revenue",
  non_operating_income: "revenue",
  cost_of_goods_sold: "expense",
  selling_admin_expenses: "expense",
  non_operating_expenses: "expense",
  manufacturing_materials: "expense",
  manufacturing_labor: "expense",
  manufacturing_overhead: "expense",
  current_assets: "asset",
  fixed_assets: "asset",
  current_liabilities: "liability",
  fixed_liabilities: "liability",
  equity: "equity",
};

/** Account types that land on the 損益計算書 (P/L) side of the 精算表 (収益 / 費用). */
export const PL_ACCOUNT_TYPES: ReadonlySet<AccountType> = new Set([
  "revenue",
  "expense",
]);

/** The P/L 段階表示, in order: `[key, 日本語ラベル, 表示区分…]` (mirrors `_PL_SECTIONS`). */
export const PL_SECTIONS: ReadonlyArray<
  readonly [string, string, ReadonlyArray<StatementCategory>]
> = [
  ["sales", "売上高", ["sales"]],
  [
    "cost_of_goods_sold",
    "売上原価",
    [
      "cost_of_goods_sold",
      "manufacturing_materials",
      "manufacturing_labor",
      "manufacturing_overhead",
    ],
  ],
  [
    "selling_admin_expenses",
    "販売費及び一般管理費",
    ["selling_admin_expenses"],
  ],
  ["non_operating_income", "営業外収益", ["non_operating_income"]],
  ["non_operating_expenses", "営業外費用", ["non_operating_expenses"]],
];

/** 表示区分 → P/L section key, derived from {@link PL_SECTIONS}. */
export const PL_CATEGORY_SECTION: ReadonlyMap<StatementCategory, string> =
  new Map(
    PL_SECTIONS.flatMap(([key, , categories]) =>
      categories.map((category) => [category, key] as const),
    ),
  );

/** B/S 表示区分 in statement layout order. */
export const ASSET_CATEGORIES: ReadonlyArray<StatementCategory> = [
  "current_assets",
  "fixed_assets",
];
export const LIABILITY_CATEGORIES: ReadonlyArray<StatementCategory> = [
  "current_liabilities",
  "fixed_liabilities",
];
export const EQUITY_CATEGORIES: ReadonlyArray<StatementCategory> = ["equity"];

/** 製造原価 区分 in form order: `[section key, 日本語ラベル, 表示区分]` (mirrors `_MANUFACTURING_SECTIONS`). */
export const MANUFACTURING_SECTIONS: ReadonlyArray<
  readonly [string, string, StatementCategory]
> = [
  ["materials", "材料費", "manufacturing_materials"],
  ["labor", "労務費", "manufacturing_labor"],
  ["overhead", "製造経費", "manufacturing_overhead"],
];

/** 表示区分 → 日本語見出し for the B/S section headings. */
export const CATEGORY_LABELS: Partial<Record<StatementCategory, string>> = {
  current_assets: "流動資産",
  fixed_assets: "固定資産",
  current_liabilities: "流動負債",
  fixed_liabilities: "固定負債",
  equity: "純資産",
};

/** 勘定科目名 of the 減価償却費 accounts (経費 7210 / 製造経費 6330 share this name). */
export const DEPRECIATION_ACCOUNT_NAME = "減価償却費";

/** 月別仕入金額 sums accounts whose 科目名 ends with this suffix (仕入高 / 原材料仕入高). */
export const PURCHASE_ACCOUNT_NAME_SUFFIX = "仕入高";

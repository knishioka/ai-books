/**
 * 決算書 → e-Tax 取込データ: mapping, schema validation, and CSV/XML rendering — the TS twin of
 * `ai_books.etax.export`.
 *
 * {@link buildEtaxExport} walks the versioned 様式 spec over the 決算書 snapshot, validates every
 * 項目 (必須・整数円・桁数・コード・月), and produces the format-neutral {@link EtaxExport}; a schema
 * fault throws {@link EtaxValidationError} with *all* problems. {@link renderEtaxCsv} /
 * {@link renderEtaxXml} are pure functions of that export, so the same records always serialize
 * the same way. {@link etaxExportSnapshot} is the canonical JSON shape the golden harness (#17)
 * freezes — the CSV/XML are deterministic functions of it.
 *
 * Amounts are emitted as **整数円** (e-Tax 取込は円単位): a 金額 carrying any 端数 (sen) is a
 * validation error rather than being silently rounded.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { formatMoney, parseMoney, type Money } from "../money";
import {
  getFormatSpec,
  LATEST_ETAX_VERSION,
  MISSING,
  resolveList,
  resolveScalar,
  type EtaxComputedField,
  type EtaxFixedSection,
  type EtaxScalarField,
  type EtaxSectionField,
  type EtaxValueKind,
} from "./spec";

interface EtaxSourceSnapshot {
  fiscal_year: string;
  start_date: string;
  end_date: string;
}

export interface EtaxRecord {
  form: string;
  itemCode: string;
  label: string;
  kind: EtaxValueKind;
  value: string;
  row: number | null;
  accountCode: string | null;
}

export interface EtaxExport {
  formatVersion: string;
  formId: string;
  fiscalYear: string;
  startDate: string;
  endDate: string;
  records: EtaxRecord[];
}

export interface EtaxProblem {
  itemCode: string;
  row: string;
  message: string;
}

/** Raised when the 決算書 maps to invalid e-Tax output; carries *all* faults at once. */
export class EtaxValidationError extends Error {
  readonly problems: EtaxProblem[];
  constructor(problems: EtaxProblem[]) {
    super(
      `e-Tax export validation failed (${problems.length} problem(s)): ` +
        problems
          .map(
            (p) =>
              `${p.itemCode}${p.row ? `[row ${p.row}]` : ""}: ${p.message}`,
          )
          .join("; "),
    );
    this.name = "EtaxValidationError";
    this.problems = problems;
  }
}

export const ETAX_FORMATS = ["csv", "xml", "xtx"] as const;
export type EtaxFormat = (typeof ETAX_FORMATS)[number];

export function parseEtaxFormat(value: string): EtaxFormat {
  if ((ETAX_FORMATS as readonly string[]).includes(value))
    return value as EtaxFormat;
  throw new Error(
    `format must be one of: ${ETAX_FORMATS.join(", ")}; got ${JSON.stringify(value)}`,
  );
}

// ── validation + rendering of a single value ─────────────────────────────────────

const ACCOUNT_CODE_RE = /^\d{3,4}$/;
const MONTH_RE = /^\d{4}-\d{2}$/;
const NUMERIC_RE = /^-?\d+(\.\d+)?$/;

type Validated =
  | [rendered: string, problem: null]
  | [rendered: null, problem: string];

function validateValue(
  raw: unknown,
  kind: EtaxValueKind,
  required: boolean,
  maxIntDigits: number,
): Validated {
  if (
    raw === MISSING ||
    raw === null ||
    (typeof raw === "string" && raw.trim() === "")
  ) {
    return required ? [null, "required value is missing or empty"] : ["", null];
  }
  const text = String(raw).trim();
  if (kind === "amount") return renderAmount(text, maxIntDigits);
  if (kind === "code") {
    return ACCOUNT_CODE_RE.test(text)
      ? [text, null]
      : [
          null,
          `invalid 勘定科目コード ${JSON.stringify(text)} (expected 3-4 digits)`,
        ];
  }
  if (kind === "month") {
    return MONTH_RE.test(text)
      ? [text, null]
      : [null, `invalid month ${JSON.stringify(text)} (expected YYYY-MM)`];
  }
  return [text, null]; // TEXT — already known non-empty here.
}

/** Validate a 金額 as whole-yen within `maxIntDigits` and render it as an integer string. */
function renderAmount(text: string, maxIntDigits: number): Validated {
  if (!NUMERIC_RE.test(text))
    return [null, `invalid amount ${JSON.stringify(text)} (not a number)`];
  const negative = text.startsWith("-");
  const body = negative ? text.slice(1) : text;
  const [intPart = "0", fracPart = ""] = body.split(".");
  if (/[^0]/.test(fracPart)) {
    return [
      null,
      `amount ${JSON.stringify(text)} is not whole yen (端数は不可)`,
    ];
  }
  const digits =
    (BigInt(intPart) === 0n ? "0" : intPart.replace(/^0+/, "")) || "0";
  if (digits.length > maxIntDigits) {
    return [
      null,
      `amount ${JSON.stringify(text)} exceeds ${maxIntDigits} integer digits`,
    ];
  }
  const integer = BigInt(intPart);
  return [
    negative && integer !== 0n ? `-${integer.toString()}` : integer.toString(),
    null,
  ];
}

// ── 決算書 → EtaxExport ───────────────────────────────────────────────────────────

export function buildEtaxExport(
  financialStatements: EtaxSourceSnapshot,
  version: string = LATEST_ETAX_VERSION,
): EtaxExport {
  const spec = getFormatSpec(version);
  const records: EtaxRecord[] = [];
  const problems: EtaxProblem[] = [];
  // sectionCode → its routed total (Σ emitted 金額); fed to later computed fields.
  const sectionTotals = new Map<string, Money>();

  for (const item of spec.items) {
    if (item.descriptor === "scalar") {
      emitScalar(item, financialStatements, records, problems);
    } else if (item.descriptor === "computed") {
      emitComputed(item, financialStatements, sectionTotals, records, problems);
    } else if (item.descriptor === "section") {
      resolveList(financialStatements, item.source).forEach((row, index) => {
        emitSectionRow(
          item.form,
          item.fields,
          row,
          index + 1,
          records,
          problems,
        );
      });
    } else {
      emitFixedSection(
        item,
        financialStatements,
        records,
        problems,
        sectionTotals,
      );
    }
  }

  if (problems.length > 0) throw new EtaxValidationError(problems);

  return {
    formatVersion: spec.version,
    formId: spec.formId,
    fiscalYear: financialStatements.fiscal_year,
    startDate: financialStatements.start_date,
    endDate: financialStatements.end_date,
    records,
  };
}

function emitScalar(
  fieldSpec: EtaxScalarField,
  snapshot: EtaxSourceSnapshot,
  records: EtaxRecord[],
  problems: EtaxProblem[],
): void {
  const raw = resolveScalar(snapshot, fieldSpec.source);
  const [rendered, problem] = validateValue(
    raw,
    fieldSpec.kind,
    fieldSpec.required,
    fieldSpec.maxIntDigits,
  );
  if (problem !== null) {
    problems.push({ itemCode: fieldSpec.itemCode, row: "", message: problem });
    return;
  }
  records.push({
    form: fieldSpec.form,
    itemCode: fieldSpec.itemCode,
    label: fieldSpec.label,
    kind: fieldSpec.kind,
    value: rendered,
    row: null,
    accountCode: null,
  });
}

/** Emit a computed scalar: a base 金額 ± earlier sections' routed totals (#83 営業外橋渡し). */
function emitComputed(
  fieldSpec: EtaxComputedField,
  snapshot: EtaxSourceSnapshot,
  sectionTotals: Map<string, Money>,
  records: EtaxRecord[],
  problems: EtaxProblem[],
): void {
  const raw = resolveScalar(snapshot, fieldSpec.baseSource);
  if (
    raw === MISSING ||
    raw === null ||
    (typeof raw === "string" && raw.trim() === "")
  ) {
    if (fieldSpec.required) {
      problems.push({
        itemCode: fieldSpec.itemCode,
        row: "",
        message: "required value is missing or empty",
      });
    }
    return;
  }
  let amount: Money;
  try {
    amount = parseMoney(raw as string | number);
  } catch {
    problems.push({
      itemCode: fieldSpec.itemCode,
      row: "",
      message: `invalid amount ${JSON.stringify(raw)} (not a number)`,
    });
    return;
  }
  for (const code of fieldSpec.addSections)
    amount += sectionTotals.get(code) ?? 0n;
  for (const code of fieldSpec.subSections)
    amount -= sectionTotals.get(code) ?? 0n;
  const [rendered, problem] = validateValue(
    formatMoney(amount),
    fieldSpec.kind,
    fieldSpec.required,
    fieldSpec.maxIntDigits,
  );
  if (problem !== null) {
    problems.push({ itemCode: fieldSpec.itemCode, row: "", message: problem });
    return;
  }
  records.push({
    form: fieldSpec.form,
    itemCode: fieldSpec.itemCode,
    label: fieldSpec.label,
    kind: fieldSpec.kind,
    value: rendered,
    row: null,
    accountCode: null,
  });
}

function emitSectionRow(
  form: string,
  fields: EtaxSectionField[],
  row: Record<string, unknown>,
  rowIndex: number,
  records: EtaxRecord[],
  problems: EtaxProblem[],
): void {
  const accountCode = rowAccountCode(fields, row);
  for (const fieldSpec of fields) {
    const raw = fieldSpec.source in row ? row[fieldSpec.source] : MISSING;
    const [rendered, problem] = validateValue(
      raw,
      fieldSpec.kind,
      fieldSpec.required,
      fieldSpec.maxIntDigits,
    );
    if (problem !== null) {
      problems.push({
        itemCode: fieldSpec.itemCode,
        row: String(rowIndex),
        message: problem,
      });
      continue;
    }
    records.push({
      form,
      itemCode: fieldSpec.itemCode,
      label: fieldSpec.label,
      kind: fieldSpec.kind,
      value: rendered,
      row: rowIndex,
      accountCode,
    });
  }
}

function rowAccountCode(
  fields: EtaxSectionField[],
  row: Record<string, unknown>,
): string | null {
  for (const fieldSpec of fields) {
    if (fieldSpec.isAccountCode) {
      const value = row[fieldSpec.source];
      return typeof value === "string" ? value : null;
    }
  }
  return null;
}

// ── 実 様式 固定勘定科目行 (EtaxFixedSection) — route snapshot lines by コード (#78) ──

/** Parse a snapshot amount into sen and apply `sign` (`as_is` / `abs` / `neg`); `null` if NaN. */
function signedAmount(
  value: unknown,
  sign: "as_is" | "abs" | "neg",
): Money | null {
  if (value === null || value === undefined) return null;
  let sen: Money;
  try {
    sen = parseMoney(value as string | number);
  } catch {
    return null;
  }
  if (sign === "abs") return sen < 0n ? -sen : sen;
  if (sign === "neg") return -sen;
  return sen;
}

/**
 * Route a 固定勘定科目行 section: lines → fixed 項目コード (summed), spillover → 追加科目枠.
 * The section's routed total (Σ fixed-row + 追加科目枠 金額) is recorded into
 * `sectionTotals[sectionCode]` for later computed fields (#83).
 */
function emitFixedSection(
  section: EtaxFixedSection,
  snapshot: EtaxSourceSnapshot,
  records: EtaxRecord[],
  problems: EtaxProblem[],
  sectionTotals?: Map<string, Money>,
): void {
  const codeToRow = new Map<string, (typeof section.rows)[number]>();
  for (const row of section.rows) {
    for (const code of row.accountCodes) codeToRow.set(code, row);
  }
  const sums = new Map<string, Money>();
  const soleCode = new Map<string, string | null>();
  const overflow: Record<string, unknown>[] = [];

  const lines = section.sources.flatMap((source) =>
    resolveList(snapshot, source),
  );
  for (const line of lines) {
    const code = line.code;
    if (typeof code === "string" && !ACCOUNT_CODE_RE.test(code)) {
      problems.push({
        itemCode: section.sectionCode,
        row: "",
        message: `invalid 勘定科目コード ${JSON.stringify(code)} (expected 3-4 digits)`,
      });
      continue;
    }
    const fixed = typeof code === "string" ? codeToRow.get(code) : undefined;
    if (!fixed) {
      overflow.push(line);
      continue;
    }
    const amount = signedAmount(line[section.valueField], fixed.sign);
    if (amount === null) {
      problems.push({
        itemCode: fixed.itemCode,
        row: "",
        message: `invalid amount ${JSON.stringify(line[section.valueField])} (not a number)`,
      });
      continue;
    }
    const first = !sums.has(fixed.itemCode);
    sums.set(fixed.itemCode, (sums.get(fixed.itemCode) ?? 0n) + amount);
    // account_code が辿れるのは その行に寄与した 科目が1つだけのとき (合算行は null).
    soleCode.set(
      fixed.itemCode,
      first ? (typeof code === "string" ? code : null) : null,
    );
  }

  let rowsTotal: Money = 0n;
  for (const row of section.rows) {
    const sum = sums.get(row.itemCode);
    if (sum !== undefined) {
      rowsTotal += sum;
      emitFixedValue(section, row.itemCode, row.label, sum, records, problems, {
        accountCode: soleCode.get(row.itemCode) ?? null,
      });
    }
  }

  const overflowTotal = emitOverflow(section, overflow, records, problems);
  sectionTotals?.set(section.sectionCode, rowsTotal + overflowTotal);
}

/**
 * Emit the section's 追加科目枠 and return the total 金額 it emitted (0 if none / dropped).
 * `accumulate` sums into one cell, `slots` fills rep-limited slots, `drop` discards 未分類 silently
 * (#83); `drop` 以外で 居場所が無い 科目 は 未分類エラー (fail-loud).
 */
function emitOverflow(
  section: EtaxFixedSection,
  overflow: Record<string, unknown>[],
  records: EtaxRecord[],
  problems: EtaxProblem[],
): Money {
  // drop モード: 様式に居場所の無い 科目 を意図的に捨てる (橋渡し用)。total には寄与しない。
  if (section.overflowMode === "drop") return 0n;

  if (overflow.length === 0) return 0n;
  if (section.overflowCode === null) {
    for (const line of overflow) {
      problems.push({
        itemCode: section.sectionCode,
        row: "",
        message: `未分類科目 ${JSON.stringify(line.code)} (${JSON.stringify(line.name)}): 対応する固定行も追加科目枠も無い`,
      });
    }
    return 0n;
  }

  if (section.overflowMode === "accumulate") {
    let total: Money = 0n;
    for (const line of overflow) {
      const amount = signedAmount(line[section.valueField], "as_is");
      if (amount === null) {
        problems.push({
          itemCode: section.overflowCode,
          row: "",
          message: `invalid amount ${JSON.stringify(line[section.valueField])} (not a number)`,
        });
        continue;
      }
      total += amount;
    }
    emitFixedValue(
      section,
      section.overflowCode,
      section.overflowLabel,
      total,
      records,
      problems,
      {},
    );
    return total;
  }

  // slots mode — each 未分類科目 takes the next 追加科目枠 slot; overflow ⇒ 未分類エラー.
  let slotsTotal: Money = 0n;
  overflow.forEach((line, index) => {
    const slot = index + 1;
    const overflowCode = section.overflowCode as string;
    if (slot > section.overflowMax) {
      problems.push({
        itemCode: overflowCode,
        row: String(slot),
        message: `追加科目枠 (${section.overflowMax}) 超過: 未分類科目 ${JSON.stringify(line.code)} (${JSON.stringify(line.name)})`,
      });
      return;
    }
    const amount = signedAmount(line[section.valueField], "as_is");
    if (amount === null) {
      problems.push({
        itemCode: overflowCode,
        row: String(slot),
        message: `invalid amount ${JSON.stringify(line[section.valueField])} (not a number)`,
      });
      return;
    }
    const name = line.name;
    const code = line.code;
    emitFixedValue(
      section,
      overflowCode,
      typeof name === "string" && name ? name : section.overflowLabel,
      amount,
      records,
      problems,
      { row: slot, accountCode: typeof code === "string" ? code : null },
    );
    slotsTotal += amount;
  });
  return slotsTotal;
}

/** Validate a summed 固定行 amount and append its record, or record a problem. */
function emitFixedValue(
  section: EtaxFixedSection,
  itemCode: string,
  label: string,
  amount: Money,
  records: EtaxRecord[],
  problems: EtaxProblem[],
  options: { row?: number; accountCode?: string | null },
): void {
  const [rendered, problem] = validateValue(
    formatMoney(amount),
    section.kind,
    true,
    section.maxIntDigits,
  );
  if (problem !== null) {
    problems.push({
      itemCode,
      row: options.row ? String(options.row) : "",
      message: problem,
    });
    return;
  }
  records.push({
    form: section.form,
    itemCode,
    label,
    kind: section.kind,
    value: rendered,
    row: options.row ?? null,
    accountCode: options.accountCode ?? null,
  });
}

// ── EtaxExport → CSV / XML / XTX / snapshot ───────────────────────────────────────

const ETAX_CSV_HEADER = [
  "面",
  "項目コード",
  "項目名",
  "行",
  "勘定科目コード",
  "値",
];

function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function csvRow(cells: string[]): string {
  return cells.map(csvCell).join(",");
}

/** Render the e-Tax 取込データ as CSV — one row per record, in spec order (CRLF line endings). */
export function renderEtaxCsv(exported: EtaxExport): string {
  const lines = [
    ETAX_CSV_HEADER,
    ...exported.records.map((r) => [
      r.form,
      r.itemCode,
      r.label,
      r.row === null ? "" : String(r.row),
      r.accountCode ?? "",
      r.value,
    ]),
  ];
  return lines.map(csvRow).join("\r\n") + "\r\n";
}

function xmlEscapeText(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function xmlEscapeAttr(value: string): string {
  return xmlEscapeText(value).replace(/"/g, "&quot;");
}

/** Render the e-Tax 取込データ as XML — a `<record>` per cell under `<etaxExport>`. */
export function renderEtaxXml(exported: EtaxExport): string {
  const attrs = (pairs: Array<[string, string]>): string =>
    pairs.map(([k, v]) => ` ${k}="${xmlEscapeAttr(v)}"`).join("");
  const rootAttrs = attrs([
    ["version", exported.formatVersion],
    ["form", exported.formId],
    ["fiscalYear", exported.fiscalYear],
    ["startDate", exported.startDate],
    ["endDate", exported.endDate],
  ]);
  const body = exported.records
    .map((record) => {
      const recordAttrs: Array<[string, string]> = [
        ["form", record.form],
        ["itemCode", record.itemCode],
        ["label", record.label],
      ];
      if (record.row !== null) recordAttrs.push(["row", String(record.row)]);
      if (record.accountCode !== null)
        recordAttrs.push(["accountCode", record.accountCode]);
      return `  <record${attrs(recordAttrs)}>${xmlEscapeText(record.value)}</record>`;
    })
    .join("\n");
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<etaxExport${rootAttrs}>\n${body}\n</etaxExport>\n`
  );
}

interface LayoutNode {
  tag: string;
  amount?: boolean;
  repeat?: boolean;
  children?: LayoutNode[];
}

interface FormLayout {
  form_id: string;
  version: string;
  namespace: string;
  pages: Array<{ tag: string; children: LayoutNode[] }>;
}

function readFormLayout(fileName: string): FormLayout {
  return JSON.parse(
    readFileSync(join(process.cwd(), "..", "src", "ai_books", "etax", fileName), "utf8"),
  ) as FormLayout;
}

const FORM_LAYOUTS: Record<string, FormLayout> = {
  KOA210: readFormLayout("koa210_layout.json"),
  KOA220: readFormLayout("koa220_layout.json"),
  KOA240: readFormLayout("koa240_layout.json"),
};

function layoutCodes(nodes: LayoutNode[], out: Set<string>): void {
  for (const node of nodes) {
    out.add(node.tag);
    if (node.children) layoutCodes(node.children, out);
  }
}

function allLayoutCodes(layout: FormLayout): Set<string> {
  const codes = new Set<string>();
  for (const page of layout.pages) layoutCodes(page.children, codes);
  return codes;
}

const FORM_CODES = new Map(
  Object.entries(FORM_LAYOUTS).map(([formId, layout]) => [
    formId,
    allLayoutCodes(layout),
  ]),
);

function selectLayout(exported: EtaxExport): FormLayout {
  const codes = new Set(exported.records.map((record) => record.itemCode));
  if (codes.size === 0) {
    throw new Error("cannot render .xtx: export has no records, so its 様式 is ambiguous");
  }
  const matches = Object.entries(FORM_LAYOUTS)
    .filter(([formId]) => {
      const layout = FORM_CODES.get(formId)!;
      return [...codes].every((code) => layout.has(code));
    })
    .map(([, layout]) => layout);
  if (matches.length === 1) return matches[0];
  if (matches.length === 0) {
    const known = new Set([...FORM_CODES.values()].flatMap((codes) => [...codes]));
    const unknown = [...codes].filter((code) => !known.has(code)).sort();
    throw new Error(
      `cannot render .xtx: 項目コード not in any known 様式 layout (${unknown.join(", ")})`,
    );
  }
  throw new Error(
    `cannot render .xtx: multiple layouts matched (${matches.map((layout) => layout.form_id).join(", ")}), making the 様式 ambiguous`,
  );
}

function descendantLeafCodes(node: LayoutNode): Set<string> {
  const codes = new Set<string>();
  if (!node.children) return codes;
  for (const child of node.children) {
    if (child.children) {
      for (const code of descendantLeafCodes(child)) codes.add(code);
    } else {
      codes.add(child.tag);
    }
  }
  return codes;
}

function renderElement(tag: string, children: string[], indent: number): string {
  const pad = "  ".repeat(indent);
  if (children.length === 0) return "";
  return `${pad}<${tag}>\n${children.join("")}${pad}</${tag}>\n`;
}

function renderLeaf(tag: string, value: string, indent: number): string {
  return `${"  ".repeat(indent)}<${tag}>${xmlEscapeText(value)}</${tag}>\n`;
}

function emitLayoutChildren(
  children: LayoutNode[],
  scalar: Map<string, string>,
  repeating: Map<string, Map<number, string>>,
  row: number | null,
  indent: number,
): string[] {
  const out: string[] = [];
  for (const node of children) {
    if (!node.children) {
      const value =
        row === null ? scalar.get(node.tag) : repeating.get(node.tag)?.get(row);
      if (value !== undefined) out.push(renderLeaf(node.tag, value, indent));
      continue;
    }
    if (node.repeat) {
      const occupied = new Set<number>();
      for (const code of descendantLeafCodes(node)) {
        for (const occurrence of repeating.get(code)?.keys() ?? []) {
          occupied.add(occurrence);
        }
      }
      for (const occurrence of [...occupied].sort((a, b) => a - b)) {
        const inner = emitLayoutChildren(
          node.children,
          scalar,
          repeating,
          occurrence,
          indent + 1,
        );
        if (inner.length > 0) out.push(renderElement(node.tag, inner, indent));
      }
      continue;
    }
    const inner = emitLayoutChildren(
      node.children,
      scalar,
      repeating,
      row,
      indent + 1,
    );
    if (inner.length > 0) out.push(renderElement(node.tag, inner, indent));
  }
  return out;
}

export function renderEtaxXtx(exported: EtaxExport): string {
  const layout = selectLayout(exported);
  const scalar = new Map<string, string>();
  const repeating = new Map<string, Map<number, string>>();
  for (const record of exported.records) {
    if (record.row === null) {
      scalar.set(record.itemCode, record.value);
    } else {
      const rows = repeating.get(record.itemCode) ?? new Map<number, string>();
      rows.set(record.row, record.value);
      repeating.set(record.itemCode, rows);
    }
  }

  const pageXml: string[] = [];
  for (const page of layout.pages) {
    const inner = emitLayoutChildren(
      page.children,
      scalar,
      repeating,
      null,
      2,
    );
    if (inner.length > 0) pageXml.push(renderElement(page.tag, inner, 1));
  }
  const rootAttrs = [
    ["xmlns", layout.namespace],
    ["VR", layout.version],
    ["softNM", "ai-books"],
    ["sakuseiNM", "ai-books"],
    ["sakuseiDay", exported.endDate],
  ] satisfies Array<[string, string]>;
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<${layout.form_id}${rootAttrs.map(([k, v]) => ` ${k}="${xmlEscapeAttr(v)}"`).join("")}>\n` +
    pageXml.join("") +
    `</${layout.form_id}>\n`
  );
}

/** Turn an {@link EtaxExport} into its canonical JSON shape (the golden harness freezes this). */
export function etaxExportSnapshot(exported: EtaxExport) {
  return {
    report: "etax_export",
    format_version: exported.formatVersion,
    form_id: exported.formId,
    fiscal_year: exported.fiscalYear,
    start_date: exported.startDate,
    end_date: exported.endDate,
    records: exported.records.map((record) => ({
      form: record.form,
      item_code: record.itemCode,
      label: record.label,
      kind: record.kind,
      row: record.row,
      account_code: record.accountCode,
      value: record.value,
    })),
  };
}

/** Render an {@link EtaxExport} to the requested concrete format. */
export function renderEtax(exported: EtaxExport, format: EtaxFormat): string {
  if (format === "csv") return renderEtaxCsv(exported);
  if (format === "xtx") return renderEtaxXtx(exported);
  return renderEtaxXml(exported);
}

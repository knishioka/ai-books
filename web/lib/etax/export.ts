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

import type { FinancialStatementsSnapshot } from "../reports/financial-statements";
import {
  getFormatSpec,
  LATEST_ETAX_VERSION,
  MISSING,
  resolveList,
  resolveScalar,
  type EtaxScalarField,
  type EtaxSectionField,
  type EtaxValueKind,
} from "./spec";

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

export const ETAX_FORMATS = ["csv", "xml"] as const;
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
  financialStatements: FinancialStatementsSnapshot,
  version: string = LATEST_ETAX_VERSION,
): EtaxExport {
  const spec = getFormatSpec(version);
  const records: EtaxRecord[] = [];
  const problems: EtaxProblem[] = [];

  for (const scalarField of spec.scalars) {
    emitScalar(scalarField, financialStatements, records, problems);
  }
  for (const section of spec.sections) {
    resolveList(financialStatements, section.source).forEach((row, index) => {
      emitSectionRow(
        section.form,
        section.fields,
        row,
        index + 1,
        records,
        problems,
      );
    });
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
  snapshot: FinancialStatementsSnapshot,
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

// ── EtaxExport → CSV / XML / snapshot ─────────────────────────────────────────────

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
  return format === "csv" ? renderEtaxCsv(exported) : renderEtaxXml(exported);
}

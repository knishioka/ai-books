import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Read-side of the golden DOM / e-Tax download E2E (issue #164).
 *
 * The golden fixtures under `tests/fixtures/seed_fy/golden/` are the repo-root SSOT (#17) the
 * Python report layer freezes and the viewer's `verify:golden` cross-check reproduces byte-for-byte.
 * Reading them straight from `web/e2e/` keeps the figures asserted *in the browser* (and the bytes
 * asserted on the e-Tax download) identical to the ones already proven elsewhere — there is no
 * second copy of the numbers to drift.
 *
 * Repo-root-relative is deliberate and safe: `e2e/` is excluded from the Next build
 * (`web/tsconfig.json` exclude + the #162 harness), so this read never runs inside the isolated
 * `web-vercel-build` web root — the CI web build never reaches up into `tests/`.
 */
const GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "tests",
  "fixtures",
  "seed_fy",
  "golden",
);

/** Parse a committed golden snapshot by name (e.g. `loadGolden("trial_balance")`). */
export function loadGolden<T = unknown>(name: string): T {
  return JSON.parse(
    readFileSync(join(GOLDEN_DIR, `${name}.json`), "utf-8"),
  ) as T;
}

/** The canonical JSON shape `etaxExportSnapshot` writes (snake_case, the golden on disk). */
interface EtaxExportSnapshot {
  format_version: string;
  form_id: string;
  fiscal_year: string;
  start_date: string;
  end_date: string;
  records: Array<{
    form: string;
    item_code: string;
    label: string;
    row: number | null;
    account_code: string | null;
    value: string;
  }>;
}

// ── e-Tax CSV / XML serialization ────────────────────────────────────────────────
//
// This deliberately MIRRORS `renderEtaxCsv` / `renderEtaxXml` in `web/lib/etax/export.ts` — the
// renderer the `/etax/download` route actually calls. We cannot import that module from a Playwright
// spec: it transitively imports the e-Tax 様式 layout `.json` files, and Playwright's ESM loader
// rejects those attribute-less JSON imports (`needs an import attribute of "type: json"`). Rather
// than reshape the production import style for a test loader, we reproduce the (tiny, stable)
// serialization here. Its exact bytes — CRLF, the header row, CSV quoting, XML escaping/attribute
// order — are pinned by `lib/etax/export.test.ts`; if the production format ever changes, the served
// body and this mirror diverge and the download spec fails, flagging that this mirror needs the same
// update. The figures themselves stay single-sourced from the golden snapshot.

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

function renderCsv(snapshot: EtaxExportSnapshot): string {
  const lines = [
    ETAX_CSV_HEADER,
    ...snapshot.records.map((r) => [
      r.form,
      r.item_code,
      r.label,
      r.row === null ? "" : String(r.row),
      r.account_code ?? "",
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

function renderXml(snapshot: EtaxExportSnapshot): string {
  const attrs = (pairs: Array<[string, string]>): string =>
    pairs.map(([k, v]) => ` ${k}="${xmlEscapeAttr(v)}"`).join("");
  const rootAttrs = attrs([
    ["version", snapshot.format_version],
    ["form", snapshot.form_id],
    ["fiscalYear", snapshot.fiscal_year],
    ["startDate", snapshot.start_date],
    ["endDate", snapshot.end_date],
  ]);
  const body = snapshot.records
    .map((record) => {
      const recordAttrs: Array<[string, string]> = [
        ["form", record.form],
        ["itemCode", record.item_code],
        ["label", record.label],
      ];
      if (record.row !== null) recordAttrs.push(["row", String(record.row)]);
      if (record.account_code !== null)
        recordAttrs.push(["accountCode", record.account_code]);
      return `  <record${attrs(recordAttrs)}>${xmlEscapeText(record.value)}</record>`;
    })
    .join("\n");
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<etaxExport${rootAttrs}>\n${body}\n</etaxExport>\n`
  );
}

/**
 * The exact bytes `/etax/download?format=…` must return for the golden export. The route builds the
 * export from the DB then renders it; here we render the *same* golden snapshot the data layer is
 * cross-checked against — so a wrong fiscal year, a swapped format, or a dropped record makes the
 * served file and this expectation diverge, and the spec fails.
 */
export function goldenEtaxBody(
  format: "csv" | "xml",
  name = "etax_export",
): string {
  const snapshot = loadGolden<EtaxExportSnapshot>(name);
  return format === "csv" ? renderCsv(snapshot) : renderXml(snapshot);
}

/** Re-exported so specs format expected figures with the *production* formatter, not a copy. */
export { formatAmount } from "../../lib/format";

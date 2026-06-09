/**
 * Golden cross-check: assert the viewer's data layer reproduces the report layer's golden
 * snapshots (#17) exactly.
 *
 * The Python side freezes one golden JSON per report from the synthetic FY2025 dataset
 * (`tests/fixtures/seed_fy/golden/*.json`) — explicitly "the shape the Vercel viewer #25 renders".
 * This script seeds those same numbers into a Postgres (`AI_BOOKS_DB_URL`), runs each `lib/reports`
 * (and `lib/etax`) builder with the same parameters the golden was generated with, and deep-compares
 * the result to the committed golden. A mismatch prints a path-tagged diff and exits non-zero, so a
 * sign flip or a dropped row is caught the same way the Python harness catches it.
 *
 * Usage (needs a DB seeded with the FY2025 fixture — see web/README.md):
 *   AI_BOOKS_DB_URL=postgres://… npx tsx scripts/verify-golden.ts
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import postgres from "postgres";

import { buildEtaxExport, etaxExportSnapshot } from "../lib/etax/export";
import { buildSampleEtaxSnapshot } from "../lib/etax/sample";
import { fetchAgriculturalIncome } from "../lib/reports/agricultural-income";
import { fetchBalanceSheet } from "../lib/reports/balance-sheet";
import { fetchFinancialStatements } from "../lib/reports/financial-statements";
import { fetchGeneralLedger } from "../lib/reports/general-ledger";
import { fetchJournalBook } from "../lib/reports/journal-book";
import { fetchMonthlyTrend } from "../lib/reports/monthly-trend";
import { fetchProfitAndLoss } from "../lib/reports/profit-and-loss";
import { fetchRealEstateIncome } from "../lib/reports/real-estate-income";
import { fetchTrialBalance } from "../lib/reports/trial-balance";
import { fetchWorksheet } from "../lib/reports/worksheet";

const GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "tests",
  "fixtures",
  "seed_fy",
  "golden",
);

const FISCAL_YEAR = "FY2025";
const START = "2025-01-01";
const END = "2025-12-31";
const KOA220_FISCAL_YEAR = {
  name: "FY2023-KOA220",
  start_date: "2023-01-01",
  end_date: "2023-12-31",
};
const KOA240_FISCAL_YEAR = {
  name: "FY2024-KOA240",
  start_date: "2024-01-01",
  end_date: "2024-12-31",
};
const MONTHLY_TREND_ACCOUNTS = ["1141", "1160", "4110", "7250"];

function loadGolden(name: string): unknown {
  return JSON.parse(readFileSync(join(GOLDEN_DIR, `${name}.json`), "utf-8"));
}

function relabelGolden(
  name: string,
  fiscalYear: { name: string; start_date: string; end_date: string },
): unknown {
  const golden = loadGolden(name);
  if (!isObject(golden)) return golden;
  return {
    ...golden,
    fiscal_year: fiscalYear.name,
    start_date: fiscalYear.start_date,
    end_date: fiscalYear.end_date,
  };
}

function balanceSheetGolden(): unknown {
  const golden = loadGolden("balance_sheet");
  return isObject(golden) ? { ...golden, as_of: END } : golden;
}

/** Path-tagged structural diff (empty ⇒ identical), mirroring the Python golden harness. */
function diff(expected: unknown, actual: unknown, path = ""): string[] {
  if (Array.isArray(expected) && Array.isArray(actual)) {
    const problems: string[] = [];
    const max = Math.max(expected.length, actual.length);
    for (let i = 0; i < max; i += 1) {
      const child = `${path}[${i}]`;
      if (i >= expected.length)
        problems.push(`${child}: unexpected (= ${JSON.stringify(actual[i])})`);
      else if (i >= actual.length) problems.push(`${child}: missing`);
      else problems.push(...diff(expected[i], actual[i], child));
    }
    return problems;
  }
  if (isObject(expected) && isObject(actual)) {
    const problems: string[] = [];
    for (const key of new Set([
      ...Object.keys(expected),
      ...Object.keys(actual),
    ])) {
      const child = path ? `${path}.${key}` : key;
      if (!(key in expected))
        problems.push(
          `${child}: unexpected (= ${JSON.stringify(actual[key])})`,
        );
      else if (!(key in actual)) problems.push(`${child}: missing`);
      else problems.push(...diff(expected[key], actual[key], child));
    }
    return problems;
  }
  return expected === actual
    ? []
    : [
        `${path || "(root)"}: ${JSON.stringify(expected)} != ${JSON.stringify(actual)}`,
      ];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function main(): Promise<void> {
  const connectionString = process.env.AI_BOOKS_DB_URL;
  if (!connectionString) {
    console.error(
      "AI_BOOKS_DB_URL is not set; point it at a DB seeded with the FY2025 fixture.",
    );
    process.exit(2);
  }
  const sql = postgres(connectionString, { max: 1, prepare: false });

  const cases: Array<[string, () => Promise<unknown>]> = [
    [
      "trial_balance",
      () =>
        fetchTrialBalance(sql, {
          fiscalYear: FISCAL_YEAR,
          start: START,
          asOf: END,
          status: "posted",
        }),
    ],
    [
      "monthly_trend",
      () =>
        fetchMonthlyTrend(sql, {
          codes: MONTHLY_TREND_ACCOUNTS,
          fiscalYear: FISCAL_YEAR,
          start: START,
          end: END,
          status: "posted",
          carryForward: false,
        }),
    ],
    [
      "journal_book",
      () => fetchJournalBook(sql, { start: START, end: END, status: "posted" }),
    ],
    [
      "general_ledger",
      () =>
        fetchGeneralLedger(sql, {
          start: START,
          end: END,
          status: "posted",
          carryForward: false,
        }),
    ],
    [
      "profit_and_loss",
      () =>
        fetchProfitAndLoss(sql, {
          fiscalYear: FISCAL_YEAR,
          start: START,
          end: END,
          status: "posted",
        }),
    ],
    [
      "balance_sheet",
      () => fetchBalanceSheet(sql, { start: START, asOf: END, status: "posted" }),
    ],
    [
      "worksheet",
      () =>
        fetchWorksheet(sql, {
          fiscalYear: FISCAL_YEAR,
          start: START,
          end: END,
          status: "posted",
        }),
    ],
    [
      "financial_statements",
      () =>
        fetchFinancialStatements(sql, {
          fiscalYear: FISCAL_YEAR,
          start: START,
          end: END,
          status: "posted",
        }),
    ],
    [
      "real_estate_income",
      () =>
        fetchRealEstateIncome(sql, {
          fiscalYear: KOA220_FISCAL_YEAR.name,
          start: KOA220_FISCAL_YEAR.start_date,
          end: KOA220_FISCAL_YEAR.end_date,
          status: "posted",
        }),
    ],
    [
      "agricultural_income",
      () =>
        fetchAgriculturalIncome(sql, {
          fiscalYear: KOA240_FISCAL_YEAR.name,
          start: KOA240_FISCAL_YEAR.start_date,
          end: KOA240_FISCAL_YEAR.end_date,
          status: "posted",
        }),
    ],
    [
      "etax_export",
      async () =>
        etaxExportSnapshot(
          buildEtaxExport(
            await fetchFinancialStatements(sql, {
              fiscalYear: FISCAL_YEAR,
              start: START,
              end: END,
              status: "posted",
            }),
          ),
        ),
    ],
    [
      "etax_export_koa220",
      () => buildSampleEtaxSnapshot(sql, KOA220_FISCAL_YEAR),
    ],
    [
      "etax_export_koa240",
      () => buildSampleEtaxSnapshot(sql, KOA240_FISCAL_YEAR),
    ],
  ];

  let failed = 0;
  for (const [name, build] of cases) {
    const actual = await build();
    const expected =
      name === "etax_export_koa220"
        ? relabelGolden(name, KOA220_FISCAL_YEAR)
        : name === "real_estate_income"
          ? relabelGolden(name, KOA220_FISCAL_YEAR)
        : name === "etax_export_koa240"
          ? relabelGolden(name, KOA240_FISCAL_YEAR)
          : name === "agricultural_income"
            ? relabelGolden(name, KOA240_FISCAL_YEAR)
          : name === "balance_sheet"
            ? balanceSheetGolden()
          : loadGolden(name);
    const problems = diff(expected, actual);
    if (problems.length === 0) {
      console.log(`✓ ${name}`);
    } else {
      failed += 1;
      console.error(`✗ ${name}: ${problems.length} difference(s)`);
      for (const problem of problems.slice(0, 20))
        console.error(`    - ${problem}`);
      if (problems.length > 20)
        console.error(`    … ${problems.length - 20} more`);
    }
  }

  await sql.end();
  if (failed > 0) {
    console.error(`\n${failed} report(s) differ from golden.`);
    process.exit(1);
  }
  console.log("\nAll reports match golden. ✅");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

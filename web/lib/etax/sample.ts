import type { Sql } from "postgres";

import { fetchAgriculturalIncome } from "../reports/agricultural-income";
import { fetchFinancialStatements } from "../reports/financial-statements";
import type { FiscalYear } from "../reports/fiscal-year";
import { fetchRealEstateIncome } from "../reports/real-estate-income";
import {
  buildEtaxExport,
  etaxExportSnapshot,
  type EtaxExport,
} from "./export";
import {
  LATEST_AGRICULTURAL_VERSION,
  LATEST_ETAX_VERSION,
  LATEST_REAL_ESTATE_VERSION,
} from "./spec";

export type SampleEtaxKind = "general" | "real_estate" | "agricultural";

export function sampleEtaxKind(fiscalYearName: string): SampleEtaxKind {
  if (fiscalYearName.includes("KOA220")) return "real_estate";
  if (fiscalYearName.includes("KOA240")) return "agricultural";
  return "general";
}

export async function buildSampleEtaxExport(
  sql: Sql,
  fiscalYear: FiscalYear,
): Promise<EtaxExport> {
  const kind = sampleEtaxKind(fiscalYear.name);
  if (kind === "real_estate") {
    return buildEtaxExport(
      await fetchRealEstateIncome(sql, {
        fiscalYear: fiscalYear.name,
        start: fiscalYear.start_date,
        end: fiscalYear.end_date,
        status: "posted",
      }),
      LATEST_REAL_ESTATE_VERSION,
    );
  }
  if (kind === "agricultural") {
    return buildEtaxExport(
      await fetchAgriculturalIncome(sql, {
        fiscalYear: fiscalYear.name,
        start: fiscalYear.start_date,
        end: fiscalYear.end_date,
        status: "posted",
      }),
      LATEST_AGRICULTURAL_VERSION,
    );
  }
  return buildEtaxExport(
    await fetchFinancialStatements(sql, {
      fiscalYear: fiscalYear.name,
      start: fiscalYear.start_date,
      end: fiscalYear.end_date,
      status: "posted",
    }),
    LATEST_ETAX_VERSION,
  );
}

export async function buildSampleEtaxSnapshot(sql: Sql, fiscalYear: FiscalYear) {
  return etaxExportSnapshot(await buildSampleEtaxExport(sql, fiscalYear));
}

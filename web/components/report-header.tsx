import type { FiscalYear } from "@/lib/reports/fiscal-year";

import { PeriodSelector } from "./period-selector";
import { PrintButton } from "./print-button";

/**
 * Shared header for a report screen: title + subtitle, the fiscal-year period selector, and the
 * print/PDF control. `period` (e.g. "2025-01-01 〜 2025-12-31" or "期末時点") is shown so the
 * reader always knows which window the figures cover.
 */
export function ReportHeader({
  title,
  subtitle,
  period,
  basePath,
  fiscalYear,
  fiscalYears,
  extra,
}: {
  title: string;
  subtitle?: string;
  period?: string;
  basePath: string;
  fiscalYear: FiscalYear;
  fiscalYears: FiscalYear[];
  extra?: Record<string, string>;
}) {
  return (
    <header className="report-header">
      <div className="report-header-titles">
        <h1>{title}</h1>
        {subtitle && <p className="report-subtitle">{subtitle}</p>}
        <p className="report-period">
          {fiscalYear.name}
          {period ? ` ｜ ${period}` : ""}
        </p>
      </div>
      <div className="report-header-controls">
        <PeriodSelector
          basePath={basePath}
          current={fiscalYear.name}
          years={fiscalYears}
          extra={extra}
        />
        <PrintButton />
      </div>
    </header>
  );
}

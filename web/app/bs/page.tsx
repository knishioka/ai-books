import type { Metadata } from "next";

import { ErrorBanner } from "@/components/banner";
import { BalanceSheetTables } from "@/components/bs-tables";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchBalanceSheet } from "@/lib/reports/balance-sheet";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "貸借対照表 | ai-books viewer",
};

export default async function BalanceSheetPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[] }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport("balance-sheet", fy, (sql, year) =>
    fetchBalanceSheet(sql, {
      start: year.start_date,
      asOf: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: bs, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="貸借対照表"
        subtitle="balance sheet（記帳確定分）"
        period={`期末 ${fiscalYear.end_date} 時点`}
        basePath="/bs"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />
      <BalanceSheetTables bs={bs} />
    </>
  );
}

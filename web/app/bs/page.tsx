import { ErrorBanner } from "@/components/banner";
import { BalanceSheetTables } from "@/components/bs-tables";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchBalanceSheet } from "@/lib/reports/balance-sheet";

export const dynamic = "force-dynamic";

export default async function BalanceSheetPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport(fy, (sql, year) =>
    fetchBalanceSheet(sql, { asOf: year.end_date, status: "posted" }),
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

import type { Metadata } from "next";

import { ErrorBanner } from "@/components/banner";
import { ProfitAndLossTable } from "@/components/pl-table";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchProfitAndLoss } from "@/lib/reports/profit-and-loss";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "損益計算書 | ai-books viewer",
};

export default async function ProfitAndLossPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[] }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport("profit-and-loss", fy, (sql, year) =>
    fetchProfitAndLoss(sql, {
      fiscalYear: year.name,
      start: year.start_date,
      end: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: pl, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="損益計算書"
        subtitle="profit & loss（段階表示）"
        period={`${pl.start_date} 〜 ${pl.end_date}`}
        basePath="/pl"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />
      <div className="card">
        <ProfitAndLossTable pl={pl} />
      </div>
    </>
  );
}

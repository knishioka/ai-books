import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchTrialBalance } from "@/lib/reports/trial-balance";

export const dynamic = "force-dynamic";

export default async function TrialBalancePage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport(fy, (sql, year) =>
    fetchTrialBalance(sql, {
      fiscalYear: year.name,
      asOf: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: tb, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="合計残高試算表"
        subtitle="trial balance（記帳確定分）"
        period={`期末 ${fiscalYear.end_date} 時点`}
        basePath="/trial-balance"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />
      <div className="card">
        <table className="report-table">
          <thead>
            <tr>
              <th>コード</th>
              <th>科目名</th>
              <th className="num">借方合計</th>
              <th className="num">貸方合計</th>
              <th className="num">残高</th>
            </tr>
          </thead>
          <tbody>
            {tb.rows.map((row) => (
              <tr key={row.code}>
                <td className="code">{row.code}</td>
                <td>{row.name}</td>
                <td className="num">
                  <Amount value={row.debit_total} />
                </td>
                <td className="num">
                  <Amount value={row.credit_total} />
                </td>
                <td className="num">
                  <Amount value={row.balance} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={2}>合計</td>
              <td className="num">
                <Amount value={tb.total_debit} />
              </td>
              <td className="num">
                <Amount value={tb.total_credit} />
              </td>
              <td className="num muted">借貸平均</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

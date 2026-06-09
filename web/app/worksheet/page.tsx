import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchWorksheet } from "@/lib/reports/worksheet";

export const dynamic = "force-dynamic";

export default async function WorksheetPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[] }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport("worksheet", fy, (sql, year) =>
    fetchWorksheet(sql, {
      fiscalYear: year.name,
      start: year.start_date,
      end: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: ws, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="精算表"
        subtitle="worksheet（残高試算表 → 修正記入 → 損益計算書 / 貸借対照表）"
        period={`${fiscalYear.start_date} 〜 ${fiscalYear.end_date}`}
        basePath="/worksheet"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />
      <div className="card scroll-x">
        <table className="report-table worksheet-table">
          <thead>
            <tr>
              <th rowSpan={2}>コード</th>
              <th rowSpan={2}>科目名</th>
              <th colSpan={2}>残高試算表</th>
              <th colSpan={2}>修正記入</th>
              <th colSpan={2}>損益計算書</th>
              <th colSpan={2}>貸借対照表</th>
            </tr>
            <tr>
              <th className="num">借方</th>
              <th className="num">貸方</th>
              <th className="num">借方</th>
              <th className="num">貸方</th>
              <th className="num">借方</th>
              <th className="num">貸方</th>
              <th className="num">借方</th>
              <th className="num">貸方</th>
            </tr>
          </thead>
          <tbody>
            {ws.rows.map((row) => (
              <tr key={row.code}>
                <td className="code">{row.code}</td>
                <td>{row.name}</td>
                <td className="num">
                  <Amount value={row.trial_debit} />
                </td>
                <td className="num">
                  <Amount value={row.trial_credit} />
                </td>
                <td className="num">
                  <Amount value={row.adjustment_debit} />
                </td>
                <td className="num">
                  <Amount value={row.adjustment_credit} />
                </td>
                <td className="num">
                  <Amount value={row.pl_debit} />
                </td>
                <td className="num">
                  <Amount value={row.pl_credit} />
                </td>
                <td className="num">
                  <Amount value={row.bs_debit} />
                </td>
                <td className="num">
                  <Amount value={row.bs_credit} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="subtotal">
              <td colSpan={2}>合計</td>
              <td className="num">
                <Amount value={ws.trial_debit_total} />
              </td>
              <td className="num">
                <Amount value={ws.trial_credit_total} />
              </td>
              <td className="num">
                <Amount value={ws.adjustment_debit_total} />
              </td>
              <td className="num">
                <Amount value={ws.adjustment_credit_total} />
              </td>
              <td className="num">
                <Amount value={ws.pl_debit_total} />
              </td>
              <td className="num">
                <Amount value={ws.pl_credit_total} />
              </td>
              <td className="num">
                <Amount value={ws.bs_debit_total} />
              </td>
              <td className="num">
                <Amount value={ws.bs_credit_total} />
              </td>
            </tr>
            <tr className="profit">
              <td colSpan={2}>当期純利益</td>
              <td className="num" colSpan={4} />
              <td className="num">
                <Amount value={ws.net_income} />
              </td>
              <td className="num" />
              <td className="num" />
              <td className="num">
                <Amount value={ws.net_income} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

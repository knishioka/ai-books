import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { buildEtaxExport, etaxExportSnapshot } from "@/lib/etax/export";
import { loadReport } from "@/lib/reports/context";
import { fetchFinancialStatements } from "@/lib/reports/financial-statements";

export const dynamic = "force-dynamic";

export default async function EtaxPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport(fy, async (sql, year) => {
    const fs = await fetchFinancialStatements(sql, {
      fiscalYear: year.name,
      start: year.start_date,
      end: year.end_date,
      status: "posted",
    });
    // build validates every 項目; a schema fault throws and surfaces in the banner.
    return etaxExportSnapshot(buildEtaxExport(fs));
  });
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: exported, fiscalYear, fiscalYears } = result.data;
  const downloadBase = `/etax/download?fy=${encodeURIComponent(fiscalYear.name)}`;

  return (
    <>
      <ReportHeader
        title="e-Tax 取込データ"
        subtitle={`${exported.form_id}（様式 ${exported.format_version}）`}
        period={`${exported.start_date} 〜 ${exported.end_date}`}
        basePath="/etax"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />

      <div className="etax-downloads">
        <a
          className="download-button"
          href={`${downloadBase}&format=csv`}
          download
        >
          CSV をダウンロード
        </a>
        <a
          className="download-button"
          href={`${downloadBase}&format=xml`}
          download
        >
          XML をダウンロード
        </a>
        <span className="muted">{exported.records.length} 項目</span>
      </div>

      <div className="card">
        <table className="report-table etax-table">
          <thead>
            <tr>
              <th>面</th>
              <th>項目コード</th>
              <th>項目名</th>
              <th className="num">行</th>
              <th>勘定科目</th>
              <th>種別</th>
              <th className="num">値</th>
            </tr>
          </thead>
          <tbody>
            {exported.records.map((record, index) => (
              <tr key={`${record.item_code}-${record.row ?? ""}-${index}`}>
                <td>{record.form}</td>
                <td className="code">{record.item_code}</td>
                <td>{record.label}</td>
                <td className="num muted">{record.row ?? ""}</td>
                <td className="code">{record.account_code ?? ""}</td>
                <td className="muted">{record.kind}</td>
                <td className="num">{record.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

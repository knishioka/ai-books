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

      <section className="etax-handoff" aria-labelledby="etax-handoff-title">
        <div>
          <h2 id="etax-handoff-title">
            e-Taxソフト(WEB版)への引き継ぎ
          </h2>
          <p>
            ダウンロードした <code>.xtx</code> は e-Taxソフト(WEB版) の
            「作成済みデータの利用」から取り込み、公式ツール側で署名して送信します。
          </p>
          <p className="muted">
            税額計算、電子署名、送信、利用者識別番号や電子証明書の取得は
            ai-books の対象外です。
          </p>
        </div>
        <dl className="etax-form-meta">
          <div>
            <dt>出力様式</dt>
            <dd>{exported.form_id}</dd>
          </div>
          <div>
            <dt>様式版</dt>
            <dd>{exported.format_version}</dd>
          </div>
          <div>
            <dt>手順</dt>
            <dd>
              <a href="https://github.com/knishioka/ai-books/blob/main/docs/etax/handoff-runbook.md">
                handoff-runbook
              </a>
            </dd>
          </div>
        </dl>
      </section>

      <div className="etax-downloads">
        <a
          className="download-button primary"
          href={`${downloadBase}&format=xml`}
          download
        >
          .xtx をダウンロード
        </a>
        <a
          className="download-button"
          href={`${downloadBase}&format=csv`}
          download
        >
          CSV をダウンロード
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

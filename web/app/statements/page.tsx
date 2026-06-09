import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { BalanceSheetTables } from "@/components/bs-tables";
import { ProfitAndLossTable } from "@/components/pl-table";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import {
  fetchFinancialStatements,
  type ManufacturingCostSectionSnapshot,
} from "@/lib/reports/financial-statements";

export const dynamic = "force-dynamic";

export default async function StatementsPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport("statements", fy, (sql, year) =>
    fetchFinancialStatements(sql, {
      fiscalYear: year.name,
      start: year.start_date,
      end: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: fs, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="青色申告決算書"
        subtitle="blue-return financial statements（プレビュー）"
        period={`${fs.start_date} 〜 ${fs.end_date}`}
        basePath="/statements"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />

      <section className="statement-face">
        <h2>1面 損益計算書</h2>
        <div className="card">
          <ProfitAndLossTable pl={fs.profit_and_loss} />
        </div>
      </section>

      <section className="statement-face">
        <h2>2面 月別売上（収入）金額及び仕入金額</h2>
        <div className="card">
          <table className="report-table">
            <thead>
              <tr>
                <th>月</th>
                <th className="num">売上（収入）金額</th>
                <th className="num">仕入金額</th>
              </tr>
            </thead>
            <tbody>
              {fs.monthly.rows.map((row) => (
                <tr key={row.month}>
                  <td className="nowrap">{row.month}</td>
                  <td className="num">
                    <Amount value={row.sales} />
                  </td>
                  <td className="num">
                    <Amount value={row.purchases} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="subtotal">
                <td>合計</td>
                <td className="num">
                  <Amount value={fs.monthly.sales_total} />
                </td>
                <td className="num">
                  <Amount value={fs.monthly.purchases_total} />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <section className="statement-face">
        <h2>3面 減価償却費の計算（直接法）</h2>
        <div className="card">
          <table className="report-table">
            <thead>
              <tr>
                <th>コード</th>
                <th>科目名</th>
                <th className="num">取得価額</th>
                <th className="num">本年分の償却費</th>
                <th className="num">期末未償却残高</th>
              </tr>
            </thead>
            <tbody>
              {fs.depreciation.lines.map((line) => (
                <tr key={line.code}>
                  <td className="code">{line.code}</td>
                  <td>{line.name}</td>
                  <td className="num">
                    <Amount value={line.acquisition_cost} />
                  </td>
                  <td className="num">
                    <Amount value={line.depreciation_expense} />
                  </td>
                  <td className="num">
                    <Amount value={line.closing_book_value} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="subtotal">
                <td colSpan={3}>本年分の償却費 合計</td>
                <td className="num">
                  <Amount value={fs.depreciation.total_depreciation} />
                </td>
                <td className="num muted">
                  PL 減価償却費 {fs.depreciation.expense_total}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <section className="statement-face">
        <h2>4面 製造原価の計算</h2>
        <div className="card">
          <table className="report-table">
            <thead>
              <tr>
                <th>コード</th>
                <th>科目名</th>
                <th className="num">金額</th>
              </tr>
            </thead>
            <tbody>
              <ManufacturingSection section={fs.manufacturing_cost.materials} />
              <ManufacturingSection section={fs.manufacturing_cost.labor} />
              <ManufacturingSection section={fs.manufacturing_cost.overhead} />
            </tbody>
            <tfoot>
              <tr className="subtotal">
                <td colSpan={2}>当期製造費用</td>
                <td className="num">
                  <Amount
                    value={fs.manufacturing_cost.total_manufacturing_cost}
                  />
                </td>
              </tr>
              <tr className="grand-total">
                <td colSpan={2}>当期製品製造原価</td>
                <td className="num">
                  <Amount
                    value={fs.manufacturing_cost.cost_of_goods_manufactured}
                  />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <section className="statement-face">
        <h2>4面 貸借対照表</h2>
        <BalanceSheetTables bs={fs.balance_sheet} />
      </section>
    </>
  );
}

function ManufacturingSection({
  section,
}: {
  section: ManufacturingCostSectionSnapshot;
}) {
  return (
    <>
      <tr className="section-head">
        <td colSpan={2}>【{section.label}】</td>
        <td className="num" />
      </tr>
      {section.lines.map((line) => (
        <tr key={line.code}>
          <td className="code">{line.code}</td>
          <td>{line.name}</td>
          <td className="num">
            <Amount value={line.amount} />
          </td>
        </tr>
      ))}
      <tr className="subtotal">
        <td colSpan={2}>{section.label} 計</td>
        <td className="num">
          <Amount value={section.subtotal} />
        </td>
      </tr>
    </>
  );
}

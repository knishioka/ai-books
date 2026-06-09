import type { Metadata } from "next";

import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { normalizeAccountCodeParam } from "@/lib/reports/filters";
import { fetchMonthlyTrend } from "@/lib/reports/monthly-trend";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "月次推移 | ai-books viewer",
};

interface AccountOption {
  code: string;
  name: string;
}

export default async function MonthlyTrendPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[]; account?: string | string[] }>;
}) {
  const { fy, account } = await searchParams;
  const accountCode = normalizeAccountCodeParam(account);

  const result = await loadReport(
    `monthly-trend:${accountCode ?? "__default__"}`,
    fy,
    async (sql, year) => {
      const accountOptions = await sql<AccountOption[]>`
        SELECT a.code, a.name
        FROM accounts a
        WHERE EXISTS (
          SELECT 1
          FROM journal_lines jl
          JOIN journal_entries je ON je.id = jl.entry_id
          WHERE jl.account_id = a.id
            AND je.entry_date <= ${year.end_date}::date
            AND je.status <> 'voided'::entry_status
        )
        ORDER BY a.code
      `;
      const codes = accountOptions.map((option) => option.code);
      // Default to 普通預金 (1141) when present, else the first touched account.
      const selected =
        accountCode && codes.includes(accountCode)
          ? accountCode
          : codes.includes("1141")
            ? "1141"
            : codes[0];
      const trend = selected
        ? await fetchMonthlyTrend(sql, {
            codes: [selected],
            fiscalYear: year.name,
            start: year.start_date,
            end: year.end_date,
            status: "posted",
            carryForward: false,
          })
        : {
            report: "monthly_trend" as const,
            fiscal_year: year.name,
            accounts: [],
          };
      return { trend, accountOptions, selected };
    },
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data, fiscalYear, fiscalYears } = result.data;
  const { trend, accountOptions, selected } = data;
  const trendAccount = trend.accounts[0];

  return (
    <>
      <ReportHeader
        title="月次推移"
        subtitle="monthly trend（期首残高 → 月次増減 → 期末残高）"
        period={`${fiscalYear.start_date} 〜 ${fiscalYear.end_date}`}
        basePath="/monthly-trend"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
        extra={selected ? { account: selected } : undefined}
      />

      <form method="get" action="/monthly-trend" className="filter-form">
        <input type="hidden" name="fy" value={fiscalYear.name} />
        <label>
          勘定科目
          <select name="account" defaultValue={selected ?? ""}>
            {accountOptions.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code} {option.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">表示</button>
      </form>

      {trendAccount ? (
        <div className="card">
          <h2 className="ledger-account-title">
            <span className="code">{trendAccount.code}</span>{" "}
            {trendAccount.name}
            <span className="ledger-account-balance muted">
              期首 <Amount value={trendAccount.opening_balance} /> ／ 期末{" "}
              <Amount value={trendAccount.closing_balance} />
            </span>
          </h2>
          <table className="report-table">
            <thead>
              <tr>
                <th scope="col">月</th>
                <th scope="col" className="num">
                  借方
                </th>
                <th scope="col" className="num">
                  貸方
                </th>
                <th scope="col" className="num">
                  純増減
                </th>
                <th scope="col" className="num">
                  残高
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="opening">
                <th scope="row">期首</th>
                <td className="num" colSpan={3}>
                  繰越
                </td>
                <td className="num">
                  <Amount value={trendAccount.opening_balance} />
                </td>
              </tr>
              {trendAccount.points.map((point) => (
                <tr key={point.month}>
                  <th scope="row" className="nowrap">
                    {point.month}
                  </th>
                  <td className="num">
                    <Amount value={point.debit_total} />
                  </td>
                  <td className="num">
                    <Amount value={point.credit_total} />
                  </td>
                  <td className="num">
                    <Amount value={point.net_change} />
                  </td>
                  <td className="num">
                    <Amount value={point.closing_balance} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="subtotal">
                <th scope="row" colSpan={4}>
                  期末残高
                </th>
                <td className="num">
                  <Amount value={trendAccount.closing_balance} />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      ) : (
        <div className="card">
          <p className="empty">対象の勘定科目に取引がありません。</p>
        </div>
      )}
    </>
  );
}

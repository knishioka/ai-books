import type { Metadata } from "next";

import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { normalizeAccountCodeParam } from "@/lib/reports/filters";
import {
  fetchGeneralLedger,
  type GeneralLedgerAccountSnapshot,
} from "@/lib/reports/general-ledger";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "総勘定元帳 | ai-books viewer",
};

interface AccountOption {
  code: string;
  name: string;
}

export default async function LedgerPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[]; account?: string | string[] }>;
}) {
  const { fy, account } = await searchParams;
  const accountCode = normalizeAccountCodeParam(account);

  const result = await loadReport(
    `ledger:${accountCode ?? "__all__"}`,
    fy,
    async (sql, year) => {
      const ledger = await fetchGeneralLedger(sql, {
        accountCode,
        start: year.start_date,
        end: year.end_date,
        status: "posted",
        carryForward: false,
      });
      const accountOptions = await sql<AccountOption[]>`
        SELECT a.code, a.name
        FROM accounts a
        WHERE EXISTS (
          SELECT 1
          FROM journal_lines jl
          JOIN journal_entries je ON je.id = jl.entry_id
          WHERE jl.account_id = a.id
            AND je.entry_date >= ${year.start_date}::date
            AND je.entry_date <= ${year.end_date}::date
            AND je.status <> 'voided'::entry_status
        )
        ORDER BY a.code
      `;
      return { ledger, accountOptions };
    },
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data, fiscalYear, fiscalYears } = result.data;
  const { ledger, accountOptions } = data;

  return (
    <>
      <ReportHeader
        title="総勘定元帳"
        subtitle="general ledger（繰越 → 期中 → 期末残高）"
        period={`${fiscalYear.start_date} 〜 ${fiscalYear.end_date}`}
        basePath="/ledger"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
        extra={accountCode ? { account: accountCode } : undefined}
      />

      <form method="get" action="/ledger" className="filter-form">
        <input type="hidden" name="fy" value={fiscalYear.name} />
        <label>
          勘定科目
          <select name="account" defaultValue={accountCode ?? ""}>
            <option value="">全科目</option>
            {accountOptions.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code} {option.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">表示</button>
      </form>

      {ledger.accounts.map((account) => (
        <AccountLedger key={account.code} account={account} />
      ))}
    </>
  );
}

function AccountLedger({ account }: { account: GeneralLedgerAccountSnapshot }) {
  return (
    <div className="card ledger-account">
      <h2 className="ledger-account-title">
        <span className="code">{account.code}</span> {account.name}
        <span className="ledger-account-balance muted">
          繰越 <Amount value={account.opening_balance} /> ／ 期末{" "}
          <Amount value={account.closing_balance} />
        </span>
      </h2>
      <div className="scroll-x">
        <table className="report-table">
        <thead>
          <tr>
            <th scope="col">日付</th>
            <th scope="col">伝票番号</th>
            <th scope="col">相手科目</th>
            <th scope="col">摘要</th>
            <th scope="col" className="num">
              借方
            </th>
            <th scope="col" className="num">
              貸方
            </th>
            <th scope="col" className="num">
              残高
            </th>
          </tr>
        </thead>
        <tbody>
          <tr className="opening">
            <th scope="row" colSpan={6}>
              前期繰越
            </th>
            <td className="num">
              <Amount value={account.opening_balance} />
            </td>
          </tr>
          {account.rows.map((row, index) => (
            <tr key={`${row.voucher_no ?? row.entry_date}-${index}`}>
              <th scope="row" className="nowrap">
                {row.entry_date}
              </th>
              <td className="code">{row.voucher_no ?? "—"}</td>
              <td className="muted">
                {row.counter_accounts.join(" / ") || "—"}
              </td>
              <td className="muted">
                {row.line_description ?? row.description ?? ""}
              </td>
              <td className="num">
                {row.side === "debit" ? <Amount value={row.amount} /> : ""}
              </td>
              <td className="num">
                {row.side === "credit" ? <Amount value={row.amount} /> : ""}
              </td>
              <td className="num">
                <Amount value={row.running_balance} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="subtotal">
            <th scope="row" colSpan={6}>
              期末残高
            </th>
            <td className="num">
              <Amount value={account.closing_balance} />
            </td>
          </tr>
        </tfoot>
      </table>
      </div>
    </div>
  );
}

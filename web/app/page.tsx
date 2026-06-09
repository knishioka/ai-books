import type { Metadata } from "next";
import Link from "next/link";

import { ErrorBanner, OkBanner } from "@/components/banner";
import { fetchAccounts, type AccountType } from "@/lib/db";
import { REPORT_ROUTES } from "@/lib/routes";

// The viewer reads live data per request; never prerender at build time (which
// also keeps `next build` from needing a database in CI).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "勘定科目 | ai-books viewer",
};

const ACCOUNT_TYPE_LABEL: Record<AccountType, string> = {
  asset: "資産",
  liability: "負債",
  equity: "純資産",
  revenue: "収益",
  expense: "費用",
};

export default async function Home() {
  const result = await fetchAccounts();

  return (
    <>
      <header className="report-header">
        <div className="report-header-titles">
          <h1>ai-books viewer</h1>
          <p className="report-subtitle">
            帳簿・集計・決算書・書類の閲覧（read-only）。数値は
            MCP/レポート層の出力と一致します。
          </p>
        </div>
      </header>

      {result.ok ? (
        <OkBanner>
          Supabase / Postgres に接続し、{result.data.length}{" "}
          件の勘定科目を取得しました。
        </OkBanner>
      ) : (
        <ErrorBanner error={result.error} />
      )}

      <section className="report-index">
        <h2>帳票一覧</h2>
        <ul className="report-index-list">
          {REPORT_ROUTES.filter((route) => route.href !== "/").map((route) => (
            <li key={route.href}>
              <Link href={route.href}>
                <span className="report-index-label">{route.label}</span>
                <span className="report-index-desc">{route.description}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>勘定科目一覧</h2>
        {result.ok &&
          (result.data.length > 0 ? (
            <div className="card">
              <table>
                <thead>
                  <tr>
                    <th scope="col">コード</th>
                    <th scope="col">科目名</th>
                    <th scope="col">区分</th>
                    <th scope="col">正常残高</th>
                    <th scope="col">状態</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.map((account) => (
                    <tr key={account.code}>
                      <td className="code">{account.code}</td>
                      <th scope="row">{account.name}</th>
                      <td>{ACCOUNT_TYPE_LABEL[account.account_type]}</td>
                      <td className="muted">
                        {account.normal_balance === "debit" ? "借方" : "貸方"}
                      </td>
                      <td className="muted">
                        {account.is_active ? "有効" : "無効"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="card">
              <p className="empty">
                勘定科目がまだ登録されていません。MCP 経由で登録してください。
              </p>
            </div>
          ))}
      </section>
    </>
  );
}

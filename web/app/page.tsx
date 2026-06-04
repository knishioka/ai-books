import { fetchAccounts, type AccountType } from "@/lib/db";

// The viewer reads live data per request; never prerender at build time (which
// also keeps `next build` from needing a database in CI).
export const dynamic = "force-dynamic";

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
    <main className="container">
      <header>
        <h1>ai-books viewer</h1>
        <p>勘定科目一覧（read-only）</p>
      </header>

      {result.ok ? (
        <div className="banner ok">
          <span className="badge">● 接続 OK</span> Supabase / Postgres
          に接続し、
          {result.data.length} 件の勘定科目を取得しました。
        </div>
      ) : (
        <div className="banner warn">
          <span className="badge">▲ 未接続</span>{" "}
          データベースに接続できませんでした。
          <br />
          <code>{result.error}</code>
        </div>
      )}

      {result.ok &&
        (result.data.length > 0 ? (
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>コード</th>
                  <th>科目名</th>
                  <th>区分</th>
                  <th>正常残高</th>
                  <th>状態</th>
                </tr>
              </thead>
              <tbody>
                {result.data.map((account) => (
                  <tr key={account.code}>
                    <td className="code">{account.code}</td>
                    <td>{account.name}</td>
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

      <footer>
        read-only viewer — データ入力は MCP 経由のみ（書込 UI なし）
      </footer>
    </main>
  );
}

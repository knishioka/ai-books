/**
 * The viewer's read-only report surface — one entry per screen, reused by the top nav and the
 * home page index. Every route renders; none writes (AGENTS.md invariant #1: no data-entry UI).
 */
export interface ReportRoute {
  href: string;
  label: string;
  description: string;
}

export const REPORT_ROUTES: ReportRoute[] = [
  {
    href: "/",
    label: "勘定科目",
    description: "勘定科目一覧（chart of accounts）",
  },
  {
    href: "/trial-balance",
    label: "試算表",
    description: "合計残高試算表（trial balance）",
  },
  {
    href: "/monthly-trend",
    label: "月次推移",
    description: "勘定科目の月次推移（monthly trend）",
  },
  { href: "/journal", label: "仕訳帳", description: "仕訳帳（journal book）" },
  {
    href: "/ledger",
    label: "総勘定元帳",
    description: "総勘定元帳（general ledger）",
  },
  {
    href: "/pl",
    label: "損益計算書",
    description: "損益計算書（profit & loss）",
  },
  {
    href: "/bs",
    label: "貸借対照表",
    description: "貸借対照表（balance sheet）",
  },
  { href: "/worksheet", label: "精算表", description: "精算表（worksheet）" },
  {
    href: "/statements",
    label: "決算書",
    description: "青色申告決算書プレビュー（blue-return）",
  },
  {
    href: "/etax",
    label: "e-Tax",
    description: "e-Tax 取込データのダウンロード",
  },
];

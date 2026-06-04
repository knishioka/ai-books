/**
 * The shared "未接続 / 未シード" banner. Every report degrades to this (instead of a stack trace)
 * when the database is unreachable, unconfigured (no `AI_BOOKS_DB_URL`, e.g. during a CI build),
 * or has no 会計年度 seeded yet.
 */
export function ErrorBanner({ error }: { error: string }) {
  return (
    <div className="banner warn">
      <span className="badge">▲ 表示できません</span> {error}
    </div>
  );
}

/** A green confirmation banner (e.g. connection OK on the home page). */
export function OkBanner({ children }: { children: React.ReactNode }) {
  return (
    <div className="banner ok">
      <span className="badge">● 接続 OK</span> {children}
    </div>
  );
}

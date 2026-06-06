import type { Metadata } from "next";

import { safeNextPath } from "@/lib/auth/allowlist";

import { signIn } from "./actions";

// The login screen reads per-request state (error / next) and must never be cached.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "ログイン — ai-books viewer",
};

/** User-facing copy for each `?error=` code the sign-in action can redirect with. */
const ERROR_MESSAGES: Record<string, string> = {
  invalid: "メールアドレスまたはパスワードが正しくありません。",
  forbidden: "このアカウントには閲覧権限がありません。",
  unconfigured:
    "認証が未設定です。NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY を設定してください。",
};

function firstParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const next = safeNextPath(firstParam(params.next));
  const errorKey = firstParam(params.error);
  // `Object.hasOwn` guards against inherited keys: a crafted `?error=toString`
  // (or `constructor`) must not resolve to a prototype member — only our own
  // message strings are valid, anything else falls through to no banner.
  const error =
    errorKey && Object.hasOwn(ERROR_MESSAGES, errorKey)
      ? ERROR_MESSAGES[errorKey]
      : undefined;

  return (
    <div className="login">
      <header className="report-header">
        <div className="report-header-titles">
          <h1>ログイン</h1>
          <p className="report-subtitle">
            認証付き read-only viewer。閲覧には Supabase Auth
            のログインが必要です。
          </p>
        </div>
      </header>

      {error ? (
        <div className="banner warn" role="alert">
          {error}
        </div>
      ) : null}

      <form className="card login-form" action={signIn}>
        <input type="hidden" name="next" value={next} />
        <label className="login-field">
          <span>メールアドレス</span>
          <input
            type="email"
            name="email"
            autoComplete="username"
            required
            autoFocus
          />
        </label>
        <label className="login-field">
          <span>パスワード</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
          />
        </label>
        <button type="submit" className="login-submit">
          ログイン
        </button>
      </form>
    </div>
  );
}

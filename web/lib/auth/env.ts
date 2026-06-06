/**
 * Auth configuration, read from the environment.
 *
 * The viewer's login gate (issue #108) is built on **Supabase Auth**. The Supabase
 * project URL and the *anon* (publishable) key are public by design — they ship to
 * the browser so the `@supabase/ssr` client can manage the session cookie — so they
 * use the `NEXT_PUBLIC_` prefix. `AUTH_ALLOWED_EMAIL` is the single-user allowlist
 * (ADR-0008): a server-only owner identity that is never exposed to the client.
 *
 * Auth ≠ multi-tenant (ADR-0008 / AGENTS.md invariant #3): authentication only gates
 * *who* may view the single owner's data; the viewer stays single-tenant and read-only,
 * and writes remain blocked by the `viewer_ro` DB role (defence in depth).
 */

export interface SupabaseAuthEnv {
  /** Supabase project URL (public). */
  url: string;
  /** Supabase anon / publishable key (public). */
  anonKey: string;
}

/**
 * The Supabase Auth public config, or `null` when it is not configured.
 *
 * Returns `null` (rather than throwing) so callers can **fail closed** gracefully: the
 * middleware redirects every protected route to `/login`, and `/login` renders a clear
 * "auth not configured" message instead of crashing. There is no anonymous fallback —
 * a viewer with no Supabase config serves no report data.
 */
export function getSupabaseAuthEnv(): SupabaseAuthEnv | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    return null;
  }
  return { url, anonKey };
}

/**
 * The configured single-user allowlist email, or `undefined` when unset.
 *
 * When set, only a session whose email matches (case-insensitively) is authorized;
 * everyone else — even with a valid Supabase token — is denied (see {@link isAllowedEmail}).
 */
export function getAllowedEmail(): string | undefined {
  const allowed = process.env.AUTH_ALLOWED_EMAIL?.trim();
  return allowed ? allowed : undefined;
}

/**
 * Single-user authorization (ADR-0008): a verified Supabase JWT proves *an* identity;
 * the allowlist proves it is *the owner's*. One principal is authorized; everyone else —
 * even with a valid Supabase session — is denied. This is **not** multi-tenancy: there is
 * one data set and one owner (AGENTS.md invariant #3).
 *
 * Pure (no env / no I/O) so the gate's decision is unit-testable in milliseconds.
 */

/**
 * Is `email` authorized to view the site?
 *
 * - When `allowed` is unset/empty, authorization falls back to "any authenticated user"
 *   (authentication alone gates; the allowlist is an optional defence-in-depth layer).
 * - When `allowed` is set, the match is case-insensitive and whitespace-trimmed, and a
 *   missing/empty `email` is denied (fail closed).
 */
export function isAllowedEmail(
  email: string | null | undefined,
  allowed: string | null | undefined,
): boolean {
  const allow = allowed?.trim();
  if (!allow) {
    return true;
  }
  const candidate = email?.trim().toLowerCase();
  if (!candidate) {
    return false;
  }
  return candidate === allow.toLowerCase();
}

/**
 * Sanitize a post-login redirect target to prevent open-redirect abuse.
 *
 * Only same-origin absolute paths are honoured; anything else (an absolute URL, a
 * protocol-relative `//host`, a missing value, or a backslash trick) falls back to `/`.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) {
    return "/";
  }
  // Reject backslashes which some browsers normalise to `/`, enabling `/\evil.com`.
  if (next.includes("\\")) {
    return "/";
  }
  return next;
}

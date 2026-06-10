import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Test identities for the E2E auth specs (issue #162).
 *
 * Both are **real Supabase Auth (GoTrue) users** — there is no test-only auth bypass
 * (AGENTS.md invariant #1 / ADR-0008 fail-closed). `OWNER_EMAIL` is the configured
 * single-user allowlist (`AUTH_ALLOWED_EMAIL`), so it is authorized; `OUTSIDER_EMAIL`
 * is a valid identity that is *not* the owner and must therefore be denied, proving the
 * allowlist is a real gate and not just authentication.
 *
 * Passwords are throwaway fixtures for an ephemeral local/CI Supabase stack — they unlock
 * nothing outside `supabase start`. They are stable constants so re-runs against a
 * persisted local stack stay idempotent.
 */
export const OWNER_EMAIL =
  process.env.AUTH_ALLOWED_EMAIL ?? "owner-e2e@ai-books.test";
export const OWNER_PASSWORD = "e2e-owner-fixture-pw";
export const OUTSIDER_EMAIL = "outsider-e2e@ai-books.test";
export const OUTSIDER_PASSWORD = "e2e-outsider-fixture-pw";

/** Service-role admin client against the local stack; never ships to the browser. */
function adminClient(): SupabaseClient {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !serviceRoleKey) {
    throw new Error(
      "E2E needs NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — run via `./scripts/test.sh --e2e`.",
    );
  }
  return createClient(url, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}

/**
 * Idempotently provision a confirmed GoTrue user via the service-role admin API.
 *
 * `email_confirm: true` skips the e-mail confirmation flow so the fixture can sign in with a
 * password immediately. If the user already exists (a persisted local stack reused across runs)
 * the password is *updated* rather than left as-is — so changing the constants above stays
 * self-healing instead of failing a later sign-in against a stale password.
 */
export async function ensureUser(
  email: string,
  password: string,
): Promise<void> {
  const admin = adminClient();
  const { data, error: listError } = await admin.auth.admin.listUsers();
  if (listError) {
    throw listError;
  }
  const existing = data.users.find((u) => u.email === email);
  if (existing) {
    const { error } = await admin.auth.admin.updateUserById(existing.id, {
      password,
    });
    if (error) {
      throw error;
    }
    return;
  }
  const { error } = await admin.auth.admin.createUser({
    email,
    password,
    email_confirm: true,
  });
  if (error) {
    throw error;
  }
}

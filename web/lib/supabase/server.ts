import "server-only";

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { getSupabaseAuthEnv } from "@/lib/auth/env";

/**
 * A request-scoped Supabase client for Server Components, Route Handlers and Server
 * Actions, wired to Next's cookie store so it can read and refresh the auth session
 * (issue #108). Used by the `/login` server actions (sign-in / sign-out); the
 * root layout reads the already-verified viewer context forwarded by `web/proxy.ts`.
 *
 * Returns `null` when Supabase Auth is not configured so callers fail closed with a
 * friendly message instead of throwing (see {@link getSupabaseAuthEnv}). The anon key is
 * public; no service-role key is ever used here — the viewer only needs to manage the
 * end-user's own session.
 */
export async function createClient() {
  const env = getSupabaseAuthEnv();
  if (!env) {
    return null;
  }

  const cookieStore = await cookies();

  return createServerClient(env.url, env.anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // `setAll` is called from a Server Component render (where cookies are
          // read-only). The session is refreshed by the middleware instead, so this
          // is safe to ignore — the standard @supabase/ssr pattern.
        }
      },
    },
  });
}

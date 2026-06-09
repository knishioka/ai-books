import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { sanitizedViewerHeaders } from "@/lib/auth/request-context";
import { getSupabaseAuthEnv } from "@/lib/auth/env";

/**
 * Result of refreshing the Supabase session inside the middleware.
 *
 * `response` carries any refreshed auth cookies and MUST be the response the middleware
 * returns (or whose cookies are copied onto a redirect) so the rotated tokens reach the
 * browser. `user` is the authenticated Supabase user, or `null` when there is no valid
 * session (unauthenticated) or auth is not configured.
 */
export interface SessionResult {
  response: NextResponse;
  user: { email?: string | null } | null;
  /** `false` when Supabase Auth env is missing — the gate must fail closed. */
  configured: boolean;
}

/**
 * Refresh the Supabase auth session for an incoming request and report the current user.
 *
 * This runs on every gated request (issue #108): it rotates the session cookie (so tokens
 * never silently expire mid-session) and reads the verified user. The caller
 * (`web/proxy.ts`) decides routing — redirect to `/login` when unauthenticated or
 * not on the single-user allowlist, otherwise let the request through.
 *
 * Fail closed: when Supabase Auth is unconfigured we return `configured: false` and no
 * user, so the middleware redirects to `/login` rather than serving report data anonymously.
 */
export async function updateSession(
  request: NextRequest,
): Promise<SessionResult> {
  let response = NextResponse.next({
    request: { headers: sanitizedViewerHeaders(request.headers) },
  });

  const env = getSupabaseAuthEnv();
  if (!env) {
    return { response, user: null, configured: false };
  }

  const supabase = createServerClient(env.url, env.anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({
          request: { headers: sanitizedViewerHeaders(request.headers) },
        });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // getUser() revalidates the token with Supabase (not just decodes the cookie), so an
  // expired/forged session is treated as unauthenticated. Do not run code between
  // createServerClient and getUser — the standard @supabase/ssr ordering.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return { response, user, configured: true };
}

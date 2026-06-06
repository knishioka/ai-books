import { NextResponse, type NextRequest } from "next/server";

import { isAllowedEmail, safeNextPath } from "@/lib/auth/allowlist";
import { getAllowedEmail } from "@/lib/auth/env";
import { updateSession } from "@/lib/supabase/middleware";

/**
 * Auth gate for the viewer (issue #108 / ADR-0008).
 *
 * Uses Next.js's `proxy` file convention (the successor to `middleware`, default in
 * Next 16). Every report route is authentication-required: an unauthenticated visitor —
 * or one whose email is not the configured single-user owner — is redirected to `/login`.
 * The gate is **fail closed**: if Supabase Auth is unconfigured, protected routes redirect
 * to `/login` rather than serving data anonymously.
 *
 * This only gates *who* may view; it is not multi-tenant. Writes remain impossible
 * regardless of login via the `viewer_ro` DB role (defence in depth) — the connection
 * string is untouched here.
 *
 * Note: the golden cross-check (`npm run verify:golden`) calls the `lib/reports/*`
 * builders directly and never issues an HTTP request, so this gate does not run on that
 * path — the numbers check stays green.
 */

const LOGIN_PATH = "/login";

/** Build a redirect that preserves any refreshed Supabase session cookies. */
function redirectTo(url: URL, cookieCarrier: NextResponse): NextResponse {
  const redirect = NextResponse.redirect(url);
  for (const cookie of cookieCarrier.cookies.getAll()) {
    redirect.cookies.set(cookie);
  }
  return redirect;
}

export async function proxy(request: NextRequest) {
  const { response, user, configured } = await updateSession(request);
  const { pathname, search } = request.nextUrl;
  const allowedEmail = getAllowedEmail();
  const authorized =
    configured && !!user && isAllowedEmail(user.email, allowedEmail);

  if (pathname === LOGIN_PATH) {
    // Already signed in and authorized → bounce away from the login screen. Build the
    // target with `new URL` so a `next` that carries a query string (e.g.
    // `/ledger?fy=FY2025`) keeps its `?…` instead of being percent-encoded into the path.
    if (authorized) {
      const target = new URL(
        safeNextPath(request.nextUrl.searchParams.get("next")),
        request.url,
      );
      return redirectTo(target, response);
    }
    return response;
  }

  if (authorized) {
    return response;
  }

  // Unauthenticated, unconfigured, or not on the allowlist → fail closed to /login.
  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = LOGIN_PATH;
  loginUrl.search = "";
  // Preserve where the visitor was headed so we can return them after login.
  loginUrl.searchParams.set("next", `${pathname}${search}`);
  if (configured && user) {
    // Authenticated but not the owner: signal it so /login explains + offers sign-out.
    loginUrl.searchParams.set("error", "forbidden");
  }
  return redirectTo(loginUrl, response);
}

export const config = {
  // Run on every route except Next internals and static assets. `/login` is handled
  // (not excluded) so an already-authenticated owner is redirected away from it.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};

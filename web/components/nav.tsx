"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { signOut } from "@/app/login/actions";
import { REPORT_ROUTES } from "@/lib/routes";

/**
 * Top navigation across the read-only report screens. Rendered once in the root layout so every
 * page shares it; hidden when printing (the print stylesheet drops `.site-nav`).
 *
 * The report links and the sign-out control are shown only to a signed-in owner (issue #108):
 * on the login screen (`userEmail == null`) only the brand renders, so the route list and the
 * sign-out form never appear to an unauthenticated visitor.
 */
export function Nav({ userEmail }: { userEmail?: string | null }) {
  const pathname = usePathname();
  const isCurrentPath = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href;

  return (
    <nav className="site-nav">
      <Link
        href="/"
        className="site-nav-brand"
        aria-current={isCurrentPath("/") ? "page" : undefined}
      >
        ai-books viewer
      </Link>
      {userEmail ? (
        <>
          <ul>
            {REPORT_ROUTES.filter((route) => route.href !== "/").map(
              (route) => (
                <li key={route.href}>
                  <Link
                    href={route.href}
                    aria-current={
                      isCurrentPath(route.href) ? "page" : undefined
                    }
                  >
                    {route.label}
                  </Link>
                </li>
              ),
            )}
          </ul>
          <div className="site-nav-user">
            <span>{userEmail}</span>
            <form action={signOut}>
              <button type="submit" className="site-nav-signout">
                ログアウト
              </button>
            </form>
          </div>
        </>
      ) : null}
    </nav>
  );
}

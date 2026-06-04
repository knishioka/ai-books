import Link from "next/link";

import { REPORT_ROUTES } from "@/lib/routes";

/**
 * Top navigation across the read-only report screens. Rendered once in the root layout so every
 * page shares it; hidden when printing (the print stylesheet drops `.site-nav`).
 */
export function Nav() {
  return (
    <nav className="site-nav">
      <Link href="/" className="site-nav-brand">
        ai-books viewer
      </Link>
      <ul>
        {REPORT_ROUTES.filter((route) => route.href !== "/").map((route) => (
          <li key={route.href}>
            <Link href={route.href}>{route.label}</Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

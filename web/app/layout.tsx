import type { Metadata } from "next";

import { Nav } from "@/components/nav";
import { createClient } from "@/lib/supabase/server";

import "./globals.css";

export const metadata: Metadata = {
  title: "ai-books viewer",
  description:
    "Authenticated read-only aggregation viewer for ai-books (Supabase/Postgres).",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // The current owner's email drives the nav (report links + sign-out appear only when
  // signed in). The auth gate itself lives in `web/middleware.ts`; this is presentation.
  const supabase = await createClient();
  let userEmail: string | null = null;
  if (supabase) {
    const {
      data: { user },
    } = await supabase.auth.getUser();
    userEmail = user?.email ?? null;
  }

  return (
    <html lang="ja">
      <body>
        <Nav userEmail={userEmail} />
        <main className="container">{children}</main>
        <footer className="site-footer">
          read-only viewer — データ入力は MCP 経由のみ（書込 UI なし）
        </footer>
      </body>
    </html>
  );
}

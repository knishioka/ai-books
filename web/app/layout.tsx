import type { Metadata } from "next";

import { Nav } from "@/components/nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "ai-books viewer",
  description: "Read-only aggregation viewer for ai-books (Supabase/Postgres).",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>
        <Nav />
        <main className="container">{children}</main>
        <footer className="site-footer">
          read-only viewer — データ入力は MCP 経由のみ（書込 UI なし）
        </footer>
      </body>
    </html>
  );
}

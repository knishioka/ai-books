import type { Metadata } from "next";

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
      <body>{children}</body>
    </html>
  );
}

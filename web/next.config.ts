import type { NextConfig } from "next";
import { join } from "node:path";

const nextConfig: NextConfig = {
  // `postgres` is a server-only dependency; keep it out of any client bundle.
  serverExternalPackages: ["postgres"],
  outputFileTracingRoot: join(process.cwd(), ".."),
  outputFileTracingIncludes: {
    "/**": ["../src/ai_books/etax/*_layout.json"],
  },
};

export default nextConfig;

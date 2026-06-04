# ai-books viewer (`web/`)

A **read-only** aggregation viewer for `ai-books`, deployed on Vercel. It renders
data stored in Supabase (Postgres) — it does **not** write anything. All
writes/validation flow through the MCP server (see [AGENTS.md](../AGENTS.md)
invariant #1: _read-only viewer only, no data-entry UI_).

- Framework: Next.js (App Router, React Server Components)
- Data: queried server-side from Postgres via [`postgres`](https://github.com/porsager/postgres); the connection string never reaches the browser.
- Current screen: chart of accounts (勘定科目一覧) + connection status.

## Local development

Prerequisites: Node 20+ and a running local Supabase (`supabase start` from the
repo root — see the root [README](../README.md#local-postgres-supabase)).

```bash
cd web
npm install
cp .env.example .env.local       # set AI_BOOKS_DB_URL (Supabase `DB URL`)
npm run dev                      # http://localhost:3000
```

`AI_BOOKS_DB_URL` defaults to the Supabase CLI's local Postgres
(`postgresql://postgres:postgres@127.0.0.1:54322/postgres`). The page shows a
connection banner: green when it reaches the database, amber (with the error)
when it cannot — so a missing DB degrades gracefully instead of crashing.

Checks:

```bash
npm run lint        # eslint (next/core-web-vitals + next/typescript)
npm run typecheck   # tsc --noEmit
npm run build       # next build (does not require a database)
```

## Deploying to Vercel

1. Create a Vercel project linked to this repo and set **Root Directory** to `web`.
2. Add the `AI_BOOKS_DB_URL` environment variable for the Preview and Production
   environments. Switching its value switches which database the viewer reads —
   e.g. a Supabase cloud connection string for Production.
3. Push a branch → Vercel builds a **Preview** deployment automatically.

### Use a read-only database role (recommended for Production)

The viewer only ever runs `SELECT`s, but for defence in depth point
`AI_BOOKS_DB_URL` at a Postgres role that _cannot_ write. Example:

```sql
CREATE ROLE viewer_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE postgres TO viewer_ro;
GRANT USAGE ON SCHEMA public TO viewer_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO viewer_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO viewer_ro;
```

Then set `AI_BOOKS_DB_URL` to that role's connection string in Vercel.

## Security notes

- `AI_BOOKS_DB_URL` is server-side only — it is never bundled into client code
  (`postgres` is listed in `serverExternalPackages`, and `lib/db.ts` imports
  `server-only`).
- No service-role key or other secret is shipped to the browser.
- There is no write UI; the viewer cannot create or edit data.

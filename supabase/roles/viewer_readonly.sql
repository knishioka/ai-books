-- Read-only viewer role for the Vercel viewer (AGENTS.md invariant #1).
--
-- GENERATED from tests/fixtures/readonly.py — do not edit by hand. Regenerate with
--   python -m tests.fixtures.readonly --write
-- tests/test_readonly_role.py fails if this file drifts from the generator or stops
-- being SELECT-only; tests/test_readonly_db.py proves the grant set against a real
-- Postgres (reads succeed, writes are rejected, future tables stay read-only).
--
-- Idempotent — safe to re-run. The viewer only ever SELECTs, but pointing its
-- AI_BOOKS_DB_URL at this role makes "cannot write" a property the database enforces,
-- not a convention. Apply it with an admin connection, e.g.
--   psql "$ADMIN_DB_URL" -v ON_ERROR_STOP=1 -f supabase/roles/viewer_readonly.sql
-- then give the role a login + password (kept OUT of version control) and point the
-- viewer at it:
--   ALTER ROLE viewer_ro WITH LOGIN PASSWORD '<strong-password>';   -- secret: .env only
--   AI_BOOKS_DB_URL=postgresql://viewer_ro:<password>@<host>:<port>/<db>

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'viewer_ro') THEN
        CREATE ROLE viewer_ro NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO viewer_ro', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO viewer_ro;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO viewer_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO viewer_ro;

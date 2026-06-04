"""Read-only viewer role grants — the single source of truth (Issue #54).

The Vercel viewer is a **read-only** surface (AGENTS.md invariant #1): every query it
runs is a ``SELECT``. Production points its ``AI_BOOKS_DB_URL`` at a Postgres role that
*cannot* write, so even a query that tried to mutate data would be rejected by the
database itself — defence in depth, not just convention.

:func:`grant_statements` renders the idempotent SQL that gives ``role`` exactly that
profile against ``schema``: ``USAGE`` on the schema, ``SELECT`` on every existing **and
future** table, and ``CONNECT`` on the current database — and nothing else. No
``INSERT`` / ``UPDATE`` / ``DELETE`` / ``TRUNCATE``, now or later.

This module is the source; two things render from it:

* ``supabase/roles/viewer_readonly.sql`` — the committed production artifact, i.e.
  :func:`render` for ``schema='public'`` / ``role='viewer_ro'``. Regenerate it with
  ``python -m tests.fixtures.readonly --write``; ``tests/test_readonly_role.py`` guards
  it against drift (and asserts it is SELECT-only) without needing a database.
* ``tests/test_readonly_db.py`` — applies :func:`grant_statements` to a throwaway schema
  and proves, against a real Postgres, that the role can run every viewer read (golden
  match included) but is denied every write.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The production role/schema the committed ``supabase/roles/viewer_readonly.sql`` targets.
PRODUCTION_ROLE = "viewer_ro"
PRODUCTION_SCHEMA = "public"

#: Where the committed rendering lives (repo-root relative).
SQL_PATH = Path(__file__).resolve().parents[2] / "supabase" / "roles" / "viewer_readonly.sql"

#: Write privileges the read-only role must never hold. Used by the tests as the
#: negative-case matrix (each must be rejected) and to scan the rendered SQL.
WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

_HEADER = """\
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
"""


def grant_statements(schema: str, role: str) -> list[str]:
    """The idempotent statements that make ``role`` SELECT-only on ``schema``.

    Returns the statements in apply order. ``schema`` and ``role`` are interpolated as
    SQL identifiers; callers pass trusted, fixed names (``public`` / ``viewer_ro`` in
    production, a throwaway schema + dedicated role in the tests), so no untrusted input
    reaches the SQL.
    """
    return [
        # Ensure the role exists. Created NOLOGIN with no password so the committed
        # script carries no secret; production grants LOGIN + PASSWORD out of band.
        f"""DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE {role} NOLOGIN;
    END IF;
END
$$;""",
        # Let the role open a connection to whichever database this runs in.
        f"""DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO {role}', current_database());
END
$$;""",
        # See the schema and read every table currently in it ...
        f"GRANT USAGE ON SCHEMA {schema} TO {role};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role};",
        # ... and every table created in it later — SELECT only, no write grant ever.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {role};",
    ]


def render(schema: str = PRODUCTION_SCHEMA, role: str = PRODUCTION_ROLE) -> str:
    """The full SQL file body (header comment + grant statements) for ``schema``/``role``."""
    return _HEADER + "\n" + "\n\n".join(grant_statements(schema, role)) + "\n"


def _write() -> None:
    SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQL_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SQL_PATH}")


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        _write()
    else:
        sys.stdout.write(render())

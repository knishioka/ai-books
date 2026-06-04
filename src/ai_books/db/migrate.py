"""Forward-only SQL migration runner.

Applies the ``*.sql`` files under ``supabase/migrations/`` in filename order,
recording each applied file in a ``schema_migrations`` table so re-runs are a
no-op (idempotent). There is no "down"/rollback step: per AGENTS.md invariant #3
the project is forward-only — to undo a change you ship a new migration that
counteracts it.

Usage::

    uv run python -m ai_books.db.migrate                  # apply pending migrations
    uv run python -m ai_books.db.migrate --migrations-dir path/to/dir
    uv run python -m ai_books.db.migrate --database-url postgresql://...

The connection string defaults to ``AI_BOOKS_DB_URL`` (see ``ai_books.db``).
Raw SQL only — no ORM (AGENTS.md invariant #4).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, cast

import psycopg

from ai_books.db import _PREPARE_THRESHOLD, get_db_url

#: Env var to override the migrations directory (mainly for tests / CI).
MIGRATIONS_DIR_ENV = "AI_BOOKS_MIGRATIONS_DIR"

#: Bookkeeping table that records which migrations have been applied.
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def default_migrations_dir() -> Path:
    """Locate the ``supabase/migrations`` directory.

    Resolution order:
    1. ``AI_BOOKS_MIGRATIONS_DIR`` if set.
    2. The nearest ``supabase/migrations`` walking up from the current directory
       (so it works when invoked from the repo root or a subdirectory).
    3. The copy relative to this source file (repo root for a source checkout).
    """
    override = os.environ.get(MIGRATIONS_DIR_ENV)
    if override:
        return Path(override)
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "supabase" / "migrations"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def discover_migrations(migrations_dir: Path) -> list[Path]:
    """Return the migration files in deterministic (filename) order."""
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {migrations_dir}")
    return sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)


def _ensure_version_table(conn: psycopg.Connection[Any]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS_DDL)


def _applied_versions(conn: psycopg.Connection[Any]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def apply_pending(conn: psycopg.Connection[Any], migrations_dir: Path) -> list[str]:
    """Apply every not-yet-applied migration on ``conn``; return the versions run.

    Each migration is applied in its own transaction together with the
    ``schema_migrations`` insert, so a failure rolls back that single file and
    leaves earlier ones durably applied. Already-applied files are skipped, which
    makes a repeat run a no-op.
    """
    _ensure_version_table(conn)
    applied = _applied_versions(conn)
    newly_applied: list[str] = []
    for path in discover_migrations(migrations_dir):
        version = path.name
        if version in applied:
            continue
        # Migration files have no bound parameters, so psycopg uses the simple
        # query protocol and can run the multi-statement script in one execute().
        sql = cast("Any", path.read_text(encoding="utf-8"))
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        newly_applied.append(version)
    return newly_applied


def run(*, db_url: str | None = None, migrations_dir: Path | None = None) -> list[str]:
    """Open a connection and apply pending migrations; return the versions run."""
    url = db_url or get_db_url()
    target = migrations_dir or default_migrations_dir()
    # prepare_threshold=None keeps migration runs pooler-safe (the repeated
    # schema_migrations INSERT would otherwise become a prepared statement that a
    # transaction-pooling proxy cannot honour); see ai_books.db (#52).
    with psycopg.connect(url, prepare_threshold=_PREPARE_THRESHOLD) as conn:
        return apply_pending(conn, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai_books.db.migrate",
        description="Apply forward-only SQL migrations (idempotent).",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=None,
        help="directory of *.sql migrations (default: auto-detect supabase/migrations)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=f"Postgres connection string (default: ${'AI_BOOKS_DB_URL'})",
    )
    args = parser.parse_args(argv)

    try:
        applied = run(db_url=args.database_url, migrations_dir=args.migrations_dir)
    except RuntimeError as exc:  # e.g. AI_BOOKS_DB_URL unset
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if applied:
        print(f"applied {len(applied)} migration(s):")
        for version in applied:
            print(f"  + {version}")
    else:
        print("no pending migrations; database is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

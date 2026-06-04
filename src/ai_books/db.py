"""Postgres connectivity for ai-books.

The system of record is Supabase (Postgres). Per AGENTS.md invariant #4
("No ORM until justified") this stays raw SQL over a thin ``psycopg`` helper.

The connection string is read from the ``AI_BOOKS_DB_URL`` environment variable
(see ``.env.example``). Locally this is the value printed by ``supabase start``;
in production it is the Supabase cloud connection string.
"""

from __future__ import annotations

import os

import psycopg

DB_URL_ENV = "AI_BOOKS_DB_URL"


def get_db_url() -> str:
    """Return the configured Postgres connection string.

    Raises:
        RuntimeError: if ``AI_BOOKS_DB_URL`` is unset or empty.
    """
    url = os.environ.get(DB_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{DB_URL_ENV} is not set. Copy .env.example to .env and point it at "
            "your Postgres (locally: the connection string from `supabase start`)."
        )
    return url


def ping() -> int:
    """Run ``SELECT 1`` against the configured database and return the value.

    A minimal connectivity smoke test. Returns ``1`` on success; propagates the
    underlying ``psycopg`` error if the database is unreachable.
    """
    with psycopg.connect(get_db_url()) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("SELECT 1 returned no rows")
        return int(row[0])

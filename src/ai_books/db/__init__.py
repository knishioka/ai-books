"""Postgres connectivity for ai-books.

The system of record is Supabase (Postgres). Per AGENTS.md invariant #4
("No ORM until justified") this stays raw SQL over a thin ``psycopg`` helper.

The connection string is read from the ``AI_BOOKS_DB_URL`` environment variable
(see ``.env.example``). Locally this is the value printed by ``supabase start``;
in production it is the Supabase cloud connection string.

Submodules:
    ``ai_books.db.migrate`` — forward-only SQL migration runner
    (``uv run python -m ai_books.db.migrate``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import DictRow, dict_row

DB_URL_ENV = "AI_BOOKS_DB_URL"

# Disable client-side prepared statements on every connection so the whole stack is
# safe behind a transaction-pooling proxy (Supabase's pooler / pgbouncer in transaction
# mode). Such a pooler routes each transaction to a possibly-different backend and so
# cannot honour a prepared statement created on an earlier one. This mirrors the viewer's
# ``prepare: false`` (web/lib/db.ts) — production reaches Postgres through the same pooler,
# so the Python side must be pooler-safe too (#52). On a direct connection the only cost is
# forgoing psycopg's prepared-statement plan cache; query results are identical.
# ``tests/test_pooler_db.py`` guards the contract: it fails if a connection that re-enables
# prepared statements is used over the pooler.
_PREPARE_THRESHOLD = None


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
    with (
        psycopg.connect(get_db_url(), prepare_threshold=_PREPARE_THRESHOLD) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT 1")
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("SELECT 1 returned no rows")
        return int(row[0])


def connect(db_url: str | None = None) -> psycopg.Connection[DictRow]:
    """Open a connection whose rows come back as ``dict``\\ s.

    ``dict_row`` lets the repository layer feed a row straight into
    ``Model.model_validate(row)`` without positional unpacking. The caller owns the
    connection's lifetime (use it as a context manager, or :func:`transaction`).
    """
    return psycopg.connect(
        db_url or get_db_url(), row_factory=dict_row, prepare_threshold=_PREPARE_THRESHOLD
    )


@contextmanager
def transaction(db_url: str | None = None) -> Iterator[psycopg.Connection[DictRow]]:
    """Yield a connection wrapped in a single transaction (the unit-of-work boundary).

    Commits when the block exits cleanly, rolls back on exception, and closes the
    connection either way. This is the one place write paths should obtain a
    connection so every logical operation is atomic.
    """
    with connect(db_url) as conn, conn.transaction():
        yield conn


__all__ = [
    "DB_URL_ENV",
    "connect",
    "get_db_url",
    "ping",
    "transaction",
]

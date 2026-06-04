"""DB-backed schema snapshot / migration drift guard (Issue #53).

Re-builds the settled schema on a throwaway schema and compares it to the
committed golden (``tests/fixtures/schema/schema.sql``). Any unintended DDL change
or apply-order break makes ``test_migrated_schema_matches_golden`` fail; the golden
is regenerated only on purpose with ``--update`` (see the module docstring).

Skips when ``AI_BOOKS_DB_URL`` is unset so ``./scripts/verify.sh`` stays green
without a live Postgres; runs in CI and under ``./scripts/test.sh``.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from ai_books import db
from ai_books.db import migrate, schema_snapshot

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
_TEST_SCHEMA = "ai_books_schema_snapshot_test"

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed schema snapshot tests",
)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    """An autocommit connection; the throwaway schema is dropped on teardown."""
    connection = psycopg.connect(db.get_db_url(), autocommit=True)
    drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(_TEST_SCHEMA))
    try:
        yield connection
    finally:
        connection.execute(drop)
        connection.close()


def test_migrated_schema_matches_golden(conn: psycopg.Connection[Any]) -> None:
    """The freshly-migrated schema must equal the committed golden snapshot."""
    snapshot = schema_snapshot.build_snapshot(
        conn, schema=_TEST_SCHEMA, migrations_dir=MIGRATIONS_DIR
    )
    golden = schema_snapshot.read_golden()

    assert golden, (
        f"golden snapshot missing: {schema_snapshot.GOLDEN_PATH}\n"
        "generate it with: uv run python -m ai_books.db.schema_snapshot --update"
    )
    if snapshot != golden:
        diff = "".join(
            difflib.unified_diff(
                golden.splitlines(keepends=True),
                snapshot.splitlines(keepends=True),
                fromfile="committed golden",
                tofile="migrated schema",
            )
        )
        pytest.fail(
            "schema drift detected — the migrated schema no longer matches "
            f"{schema_snapshot.GOLDEN_PATH}.\n"
            "If intentional, regenerate with: "
            "uv run python -m ai_books.db.schema_snapshot --update\n\n"
            f"{diff}"
        )


def test_snapshot_is_schema_name_independent(conn: psycopg.Connection[Any]) -> None:
    """Building in two differently-named schemas yields byte-identical snapshots.

    Guards Issue #53 AC: the dump must not be influenced by environment differences
    such as the (throwaway) schema name it was produced in.
    """
    other_schema = f"{_TEST_SCHEMA}_alt"
    drop_other = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(other_schema))
    try:
        first = schema_snapshot.build_snapshot(
            conn, schema=_TEST_SCHEMA, migrations_dir=MIGRATIONS_DIR
        )
        second = schema_snapshot.build_snapshot(
            conn, schema=other_schema, migrations_dir=MIGRATIONS_DIR
        )
        assert first == second
    finally:
        conn.execute(drop_other)


def test_drift_is_detected(conn: psycopg.Connection[Any]) -> None:
    """An out-of-band DDL change must make the dump diverge from the golden."""
    # Stand up a migrated schema by hand (build_snapshot drops on exit), then
    # mutate the DDL so the dump no longer matches the committed golden.
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(_TEST_SCHEMA)))
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(_TEST_SCHEMA)))
    migrate.apply_pending(conn, MIGRATIONS_DIR)
    conn.execute("ALTER TABLE accounts ADD COLUMN drift_marker text")
    drifted = schema_snapshot.dump_schema(conn, _TEST_SCHEMA)

    assert "drift_marker" in drifted
    assert drifted != schema_snapshot.read_golden()

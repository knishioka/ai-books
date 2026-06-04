"""DB-backed tests for the append-only audit helper.

Skips without ``AI_BOOKS_DB_URL``. Verifies ``record_audit`` appends a row and that
the append-only guarantee (invariant #5) holds — update/delete are rejected.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.audit import record_audit

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed audit tests",
)


def test_record_audit_appends_row(migrated_conn: psycopg.Connection[Any]) -> None:
    log = record_audit(
        migrated_conn,
        actor="agent",
        action="insert",
        tool_name="create_account",
        table_name="accounts",
        record_id=1,
        after={"code": "1110", "name": "現金"},
    )

    assert log.id is not None
    assert log.actor == "agent"
    assert log.record_id == "1"  # stringified
    assert log.after == {"code": "1110", "name": "現金"}
    assert log.created_at is not None

    row = migrated_conn.execute("SELECT count(*) AS n FROM audit_logs").fetchone()
    assert row is not None
    assert row["n"] == 1


def test_record_audit_handles_null_snapshots(migrated_conn: psycopg.Connection[Any]) -> None:
    log = record_audit(migrated_conn, actor="agent", action="post")
    assert log.before is None
    assert log.after is None
    assert log.record_id is None


def test_audit_log_is_append_only(migrated_conn: psycopg.Connection[Any]) -> None:
    record_audit(migrated_conn, actor="agent", action="insert")

    with pytest.raises(psycopg.Error, match="append-only"):
        migrated_conn.execute("UPDATE audit_logs SET actor = 'x'")
    with pytest.raises(psycopg.Error, match="append-only"):
        migrated_conn.execute("DELETE FROM audit_logs")

    row = migrated_conn.execute("SELECT count(*) AS n FROM audit_logs").fetchone()
    assert row is not None
    assert row["n"] == 1  # the original row survived both attempts

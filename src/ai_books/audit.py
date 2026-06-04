"""Append-only audit logging helper.

Every write path (MCP tool) records what it did here. Per AGENTS.md invariant #5
the audit trail is *append-only*: this module only ever ``INSERT``\\ s — there is no
update/delete path, and the ``audit_logs`` table backs that up with triggers that
reject mutation. Keeping the only writer in one place makes the guarantee easy to
audit by reading a single function.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ai_books.errors import RepositoryError
from ai_books.models import AuditLog


def record_audit(
    conn: psycopg.Connection[Any],
    *,
    actor: str,
    action: str,
    tool_name: str | None = None,
    table_name: str | None = None,
    record_id: str | int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit-log row and return it as an :class:`AuditLog` model.

    Args:
        conn: an open connection; the caller owns the transaction boundary so the
            audit row commits or rolls back together with the change it describes.
        actor: who performed the action (AI agent / user identifier).
        action: the logical operation (``insert`` / ``update`` / ``post`` …).
        tool_name: the MCP tool the write came through, if any.
        table_name / record_id: what was touched (``record_id`` is stringified so any
            key type fits the text column).
        before / after: JSON snapshots of the row around the change.

    Raises:
        RepositoryError: if the insert somehow returns no row.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO audit_logs
                (actor, tool_name, action, table_name, record_id, before, after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                actor,
                tool_name,
                action,
                table_name,
                None if record_id is None else str(record_id),
                _as_jsonb(before),
                _as_jsonb(after),
            ),
        )
        row = cur.fetchone()
    if row is None:  # pragma: no cover - RETURNING always yields a row
        raise RepositoryError("audit_logs INSERT ... RETURNING produced no row")
    return AuditLog.model_validate(row)


def _as_jsonb(value: dict[str, Any] | None) -> Jsonb | None:
    """Wrap a dict so psycopg writes it to a ``jsonb`` column (``None`` stays NULL)."""
    if value is None:
        return None
    # Fail fast on non-serialisable snapshots rather than at the driver boundary.
    json.dumps(value)
    return Jsonb(value)
